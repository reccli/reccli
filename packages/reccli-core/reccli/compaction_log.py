"""
Compaction Log - Safety net for compaction operations

Append-only log of all compaction operations enabling recovery if reindexing fails.
Provides rollback capability and audit trail.

Log format: .devsession-compaction-log.jsonl (JSON Lines)
"""

from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
import json
import hashlib
import shutil


class CompactionLog:
    """Manage compaction safety log"""

    def __init__(self, session_path: Path):
        """
        Initialize compaction log for a session

        Args:
            session_path: Path to .devsession file

        The log will be created at: <session_path>-compaction-log.jsonl
        Example: my-session.devsession-compaction-log.jsonl
        """
        self.session_path = Path(session_path)
        self.log_path = self.session_path.parent / f"{self.session_path.stem}-compaction-log.jsonl"

    def compute_checksum(self, session_data: Dict) -> str:
        """
        Compute checksum of session data

        Args:
            session_data: .devsession dict

        Returns:
            SHA256 checksum
        """
        # Serialize to JSON (deterministic)
        json_str = json.dumps(session_data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()[:16]

    def log_compaction_start(
        self,
        session_data_before: Dict,
        plan: Dict[str, Any]
    ) -> str:
        """
        Log the start of a compaction operation

        Args:
            session_data_before: Session before compaction
            plan: Compaction plan with keys:
                - removed_ranges: List of [start_index, end_index] ranges to remove
                - kept_ranges: List of [start_index, end_index] ranges to keep
                - reason: Reason for compaction (e.g., "token_limit", "manual")
                - tokens_before: Token count before
                - expected_tokens_after: Expected token count after

        Returns:
            Operation ID for tracking
        """
        # Generate operation ID
        timestamp = datetime.utcnow()
        operation_id = f"compact_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        # Compute checksum
        checksum_before = self.compute_checksum(session_data_before)

        # Log entry
        entry = {
            "timestamp": timestamp.isoformat() + "Z",
            "operation": "compaction_start",
            "operation_id": operation_id,
            "checksum_before": checksum_before,
            "plan": plan,
            "backup_created": False  # Will be updated by create_backup()
        }

        self._append_entry(entry)

        return operation_id

    def create_backup(
        self,
        operation_id: str,
        session_data: Dict
    ) -> Path:
        """
        Create backup of session before compaction

        Args:
            operation_id: Operation ID from log_compaction_start()
            session_data: Session to backup

        Returns:
            Path to backup file
        """
        # Backup path: <session>-backup-<operation_id>.devsession
        backup_path = self.session_path.parent / f"{self.session_path.stem}-backup-{operation_id}.devsession"

        # Write backup
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        # Log backup creation
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "backup_created",
            "operation_id": operation_id,
            "backup_path": str(backup_path)
        }

        self._append_entry(entry)

        return backup_path

    def log_reindexing(
        self,
        operation_id: str,
        summary_items_updated: int,
        warnings: List[str]
    ) -> None:
        """
        Log reindexing results

        Args:
            operation_id: Operation ID
            summary_items_updated: Number of items reindexed
            warnings: Warnings from reindexing
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "reindexing",
            "operation_id": operation_id,
            "summary_items_updated": summary_items_updated,
            "warnings": warnings
        }

        self._append_entry(entry)

    def log_validation(
        self,
        operation_id: str,
        validation_passed: bool,
        errors: List[str]
    ) -> None:
        """
        Log validation results

        Args:
            operation_id: Operation ID
            validation_passed: Whether validation passed
            errors: Validation errors (if any)
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "validation",
            "operation_id": operation_id,
            "validation_passed": validation_passed,
            "errors": errors
        }

        self._append_entry(entry)

    def log_compaction_complete(
        self,
        operation_id: str,
        session_data_after: Dict,
        success: bool,
        error: Optional[str] = None
    ) -> None:
        """
        Log completion of compaction

        Args:
            operation_id: Operation ID
            session_data_after: Session after compaction
            success: Whether operation succeeded
            error: Error message if failed
        """
        checksum_after = self.compute_checksum(session_data_after) if success else None

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "compaction_complete",
            "operation_id": operation_id,
            "success": success,
            "checksum_after": checksum_after,
            "error": error
        }

        self._append_entry(entry)

    def log_rollback(
        self,
        operation_id: str,
        reason: str
    ) -> None:
        """
        Log rollback of compaction

        Args:
            operation_id: Operation ID
            reason: Reason for rollback
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "operation": "rollback",
            "operation_id": operation_id,
            "reason": reason
        }

        self._append_entry(entry)

    def rollback_to_backup(self, operation_id: str) -> Path:
        """
        Rollback to backup from operation

        Args:
            operation_id: Operation ID to rollback

        Returns:
            Path to restored session

        Raises:
            FileNotFoundError: If backup doesn't exist
        """
        # Find backup path from log
        backup_path = None

        if self.log_path.exists():
            with open(self.log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    entry = json.loads(line)
                    if (entry.get("operation") == "backup_created" and
                        entry.get("operation_id") == operation_id):
                        backup_path = Path(entry["backup_path"])
                        break

        if not backup_path or not backup_path.exists():
            raise FileNotFoundError(f"No backup found for operation {operation_id}")

        # Restore backup
        shutil.copy(backup_path, self.session_path)

        # Log rollback
        self.log_rollback(operation_id, "Manual rollback")

        return self.session_path

    def get_last_successful_compaction(self) -> Optional[Dict]:
        """
        Get last successful compaction operation

        Returns:
            Last successful operation entry or None
        """
        if not self.log_path.exists():
            return None

        last_success = None

        with open(self.log_path, 'r', encoding='utf-8') as f:
            for line in f:
                entry = json.loads(line)
                if (entry.get("operation") == "compaction_complete" and
                    entry.get("success") == True):
                    last_success = entry

        return last_success

    def get_compaction_history(self, limit: int = 10) -> List[Dict]:
        """
        Get compaction history

        Args:
            limit: Max number of operations to return

        Returns:
            List of compaction operations (newest first)
        """
        if not self.log_path.exists():
            return []

        operations = {}

        # Read all entries
        with open(self.log_path, 'r', encoding='utf-8') as f:
            for line in f:
                entry = json.loads(line)
                op_id = entry.get("operation_id")

                if not op_id:
                    continue

                if op_id not in operations:
                    operations[op_id] = {
                        "operation_id": op_id,
                        "events": []
                    }

                operations[op_id]["events"].append(entry)

        # Sort by timestamp (newest first)
        sorted_ops = sorted(
            operations.values(),
            key=lambda x: x["events"][0]["timestamp"],
            reverse=True
        )

        return sorted_ops[:limit]

    def cleanup_old_backups(self, keep: int = 5) -> List[Path]:
        """
        Remove old backup files, keeping N most recent

        Args:
            keep: Number of backups to keep

        Returns:
            List of deleted backup paths
        """
        # Find all backup files
        pattern = f"{self.session_path.stem}-backup-*.devsession"
        backups = list(self.session_path.parent.glob(pattern))

        # Sort by modification time (newest first)
        backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # Delete old backups
        deleted = []
        for backup in backups[keep:]:
            backup.unlink()
            deleted.append(backup)

        return deleted

    def _append_entry(self, entry: Dict) -> None:
        """
        Append entry to log file

        Args:
            entry: Log entry dict
        """
        # Create log file if doesn't exist
        if not self.log_path.exists():
            self.log_path.touch()

        # Append entry
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    def print_history(self, limit: int = 10) -> None:
        """
        Print compaction history (human-readable)

        Args:
            limit: Max operations to show
        """
        operations = self.get_compaction_history(limit)

        if not operations:
            print("No compaction history found")
            return

        print(f"# Compaction History ({len(operations)} operations)\n")

        for op in operations:
            print(f"## {op['operation_id']}")

            for event in op['events']:
                timestamp = event['timestamp'][:19]  # Remove Z
                operation = event['operation']

                if operation == "compaction_start":
                    plan = event.get('plan', {})
                    print(f"  {timestamp} - START")
                    print(f"    Reason: {plan.get('reason', 'unknown')}")
                    print(f"    Tokens: {plan.get('tokens_before', '?')} → {plan.get('expected_tokens_after', '?')}")

                elif operation == "backup_created":
                    print(f"  {timestamp} - BACKUP: {event.get('backup_path', '?')}")

                elif operation == "reindexing":
                    print(f"  {timestamp} - REINDEX: {event.get('summary_items_updated', 0)} items")
                    if event.get('warnings'):
                        print(f"    Warnings: {len(event['warnings'])}")

                elif operation == "validation":
                    status = "✅ PASS" if event.get('validation_passed') else "❌ FAIL"
                    print(f"  {timestamp} - VALIDATE: {status}")
                    if event.get('errors'):
                        print(f"    Errors: {len(event['errors'])}")

                elif operation == "compaction_complete":
                    status = "✅ SUCCESS" if event.get('success') else "❌ FAILED"
                    print(f"  {timestamp} - COMPLETE: {status}")
                    if event.get('error'):
                        print(f"    Error: {event['error']}")

                elif operation == "rollback":
                    print(f"  {timestamp} - ROLLBACK: {event.get('reason', 'unknown')}")

            print()
