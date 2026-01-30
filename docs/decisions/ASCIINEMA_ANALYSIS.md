# Asciinema Architecture Analysis
## For .devsession Integration (Phase 0)

**Date**: 2025-11-01
**Purpose**: Understand asciinema's Rust codebase to create custom .devsession writer

---

## 🔍 Key Discovery

**Asciinema is written in Rust** (not Python as originally thought). This changes our approach but is actually beneficial - Rust's type system and performance will serve us well.

---

## 📐 Architecture Overview

### Core Flow

```
Terminal (PTY) → Session → Event Stream → Encoder → File Writer → .cast file
```

Detailed breakdown:

```rust
1. PTY Capture (src/tty/)
   ├─ Captures terminal I/O using Unix PTY
   ├─ Reads output from shell
   └─ Writes input from user

2. Session Manager (src/session.rs)
   ├─ Coordinates PTY with event stream
   ├─ Generates Event objects
   └─ Forwards to Output implementations

3. Events (src/session.rs)
   pub enum Event {
       Output(Duration, String),   // Terminal output
       Input(Duration, String),    // User input
       Resize(Duration, TtySize),  // Window resize
       Marker(Duration, String),   // User markers
       Exit(Duration, i32),        // Process exit
   }

4. File Writer (src/file_writer.rs)
   ├─ Implements session::Output trait
   ├─ Uses an Encoder to format events
   └─ Writes to file asynchronously

5. Encoder (src/encoder/, src/asciicast.rs)
   ├─ V2Encoder → .cast format (JSON Lines)
   ├─ V3Encoder → .cast format (binary)
   └─ TextEncoder → plain text

6. Output
   .cast file (asciinema format)
```

---

## 🎯 Key Files for .devsession Integration

### 1. `src/file_writer.rs` (116 lines) ⭐ PRIMARY TARGET

```rust
pub struct FileWriter {
    writer: Box<dyn AsyncWrite + Send + Unpin>,
    encoder: Box<dyn Encoder + Send>,  // ← We'll create DevsessionEncoder
    notifier: Box<dyn Notifier>,
    metadata: Metadata,
}

impl FileWriter {
    // Writes header at start
    pub async fn start(mut self) -> io::Result<LiveFileWriter> {
        let header = asciicast::Header { ... };
        self.writer.write_all(&self.encoder.header(&header)).await
    }
}

#[async_trait]
impl session::Output for LiveFileWriter {
    // Writes each event as it happens
    async fn event(&mut self, event: session::Event) -> io::Result<()> {
        self.writer.write_all(&self.encoder.event(event.into())).await
    }

    async fn flush(&mut self) -> io::Result<()> {
        self.writer.write_all(&self.encoder.flush()).await
    }
}
```

**What this means**: FileWriter is ALREADY generic - it accepts any `Encoder`. We just need to create a `DevsessionEncoder`!

### 2. `src/encoder/mod.rs` (38 lines) ⭐ ENCODER INTERFACE

```rust
pub trait Encoder {
    fn header(&mut self, header: &Header) -> Vec<u8>;
    fn event(&mut self, event: Event) -> Vec<u8>;
    fn flush(&mut self) -> Vec<u8>;
}
```

**What this means**: Our `DevsessionEncoder` just needs to implement these 3 methods!

### 3. `src/asciicast.rs` (615 lines) - EVENT DEFINITIONS

```rust
pub struct Header {
    pub term_cols: u16,
    pub term_rows: u16,
    pub term_type: Option<String>,
    pub term_version: Option<String>,
    pub term_theme: Option<TtyTheme>,
    pub timestamp: Option<u64>,
    pub idle_time_limit: Option<f64>,
    pub command: Option<String>,
    pub title: Option<String>,
    pub env: Option<HashMap<String, String>>,
}

pub struct Event {
    pub time: Duration,
    pub data: EventData,
}

pub enum EventData {
    Output(String),      // "o"
    Input(String),       // "i"
    Resize(u16, u16),    // "r"
    Marker(String),      // "m"
    Exit(i32),           // "x"
    Other(char, String),
}
```

### 4. `src/session.rs` (343 lines) - SESSION ORCHESTRATION

The core session runner that:
- Spawns PTY
- Captures I/O
- Generates events
- Forwards to Output implementations (like our FileWriter)

**We don't need to modify this** - it's already perfect for our needs.

---

## 🛠️ Implementation Strategy

### Option 1: Create Custom Encoder (RECOMMENDED ✅)

**Approach**: Add a new encoder to asciinema that outputs .devsession format

**Steps**:

1. Create `src/encoder/devsession.rs`
2. Implement `Encoder` trait
3. Output JSON in .devsession format
4. Register in `src/encoder/mod.rs`
5. Add CLI flag: `asciinema rec --format devsession output.devsession`

**Pros**:
- ✅ Minimal changes to asciinema codebase
- ✅ Leverages existing PTY capture (battle-tested)
- ✅ Clean separation of concerns
- ✅ Can contribute back to asciinema project
- ✅ Easy to maintain

**Cons**:
- ❌ Requires understanding Rust (but we can handle this)
- ❌ Dependency on asciinema architecture

**Code Required**: ~200 lines of Rust

### Option 2: Fork Entire Project

**Approach**: Fork asciinema, rename to RecCli, modify heavily

**Pros**:
- ✅ Full control

**Cons**:
- ❌ More code to maintain
- ❌ Can't easily merge upstream improvements
- ❌ Overkill for our needs

---

## 📝 .devsession Encoder Implementation Plan

### File: `src/encoder/devsession.rs`

```rust
use serde_json::json;
use std::time::Duration;
use crate::asciicast::{Event, EventData, Header};
use crate::encoder::Encoder;

pub struct DevsessionEncoder {
    events: Vec<serde_json::Value>,  // Buffer events
    start_time: Option<Duration>,
}

impl DevsessionEncoder {
    pub fn new() -> Self {
        DevsessionEncoder {
            events: Vec::new(),
            start_time: None,
        }
    }
}

impl Encoder for DevsessionEncoder {
    fn header(&mut self, header: &Header) -> Vec<u8> {
        // Store header info, but don't write yet
        // We'll write everything in flush()
        self.start_time = header.timestamp.map(|t| Duration::from_secs(t));
        Vec::new()  // Return empty - we write at flush()
    }

    fn event(&mut self, event: Event) -> Vec<u8> {
        // Buffer event in .devsession format
        let (event_type, data) = match event.data {
            EventData::Output(s) => ("o", s),
            EventData::Input(s) => ("i", s),
            EventData::Resize(cols, rows) => ("r", format!("{}x{}", cols, rows)),
            EventData::Marker(s) => ("m", s),
            EventData::Exit(code) => ("x", code.to_string()),
            EventData::Other(c, s) => (&c.to_string()[..], s),
        };

        let event_json = json!([
            event.time.as_secs_f64(),
            event_type,
            data
        ]);

        self.events.push(event_json);
        Vec::new()  // Return empty - we write at flush()
    }

    fn flush(&mut self) -> Vec<u8> {
        // Write complete .devsession file
        let devsession = json!({
            "format": "devsession",
            "version": "1.0",
            "session_id": format!("session_{}", chrono::Utc::now().timestamp()),
            "created": chrono::Utc::now().to_rfc3339(),

            "terminal_recording": {
                "version": 2,
                "width": 80,  // Get from header
                "height": 24,  // Get from header
                "events": self.events
            },

            // These will be populated later by RecCli
            "conversation": [],
            "summary": null,
            "vector_index": null
        });

        serde_json::to_vec_pretty(&devsession)
            .unwrap_or_default()
    }
}
```

### Modification to `src/encoder/mod.rs`

```rust
mod devsession;  // Add this

pub use devsession::DevsessionEncoder;  // Add this

// In encoder() function
pub fn encoder(version: Version) -> Option<Box<dyn Encoder>> {
    match version {
        Version::One => None,
        Version::Two => Some(Box::new(V2Encoder::new(Duration::from_micros(0)))),
        Version::Three => Some(Box::new(V3Encoder::new())),
        Version::Devsession => Some(Box::new(DevsessionEncoder::new())),  // Add this
    }
}
```

---

## 🎯 Integration with RecCli

### Current RecCli (Python) + Modified asciinema (Rust)

**Approach**:

1. Modify asciinema (Rust) to support .devsession output
2. Compile asciinema as binary: `/usr/local/bin/asciinema`
3. RecCli (Python) calls asciinema subprocess:
   ```python
   subprocess.run(['asciinema', 'rec', '--format', 'devsession', 'output.devsession'])
   ```
4. RecCli reads `.devsession` file and adds:
   - Parsed conversation
   - AI summary
   - Vector embeddings

**Workflow**:

```
User clicks "Record" in RecCli UI
   ↓
RecCli spawns: asciinema rec --format devsession session.devsession
   ↓
asciinema captures terminal → writes .devsession (terminal_recording layer)
   ↓
User clicks "Stop"
   ↓
RecCli reads .devsession file
RecCli parses conversation from terminal events
RecCli generates summary (if requested)
RecCli adds embeddings (if requested)
RecCli saves complete .devsession
```

---

## 🚀 Phase 0 Tasks (Revised)

Based on this analysis:

### ✅ Completed:
- [x] Clone asciinema repository
- [x] Study architecture
- [x] Understand encoder pattern
- [x] Identify modification points

### ⏸️ Next Steps:

1. **Set up Rust environment** (if not already)
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

2. **Create DevsessionEncoder**
   - Create `/Users/will/coding-projects/RecCli/asciinema/src/encoder/devsession.rs`
   - Implement the Encoder trait
   - Add .devsession format output

3. **Modify encoder/mod.rs**
   - Add devsession module
   - Export DevsessionEncoder

4. **Test build**
   ```bash
   cd asciinema
   cargo build --release
   ```

5. **Test recording**
   ```bash
   ./target/release/asciinema rec --format devsession test.devsession
   ```

6. **Verify .devsession output**
   - Check JSON structure
   - Verify terminal events captured
   - Confirm playback compatibility

---

## 📊 Complexity Assessment

| Task | Complexity | Time | Status |
|------|-----------|------|--------|
| Understanding asciinema architecture | Medium | 2h | ✅ Done |
| Setting up Rust environment | Easy | 15min | ⏸️ Next |
| Creating DevsessionEncoder | Medium | 3-4h | ⏸️ |
| Testing & debugging | Medium | 2h | ⏸️ |
| Integration with RecCli Python | Easy | 1h | ⏸️ |

**Total Phase 0 Time**: ~8-9 hours

---

## 🎉 Key Insights

1. **asciinema's architecture is PERFECT for our needs**
   - Already captures everything we need
   - Clean encoder pattern for custom formats
   - Minimal modifications required

2. **We don't need to rewrite PTY capture**
   - asciinema handles all edge cases
   - Battle-tested across platforms
   - Reliable and performant

3. **The Encoder trait is our golden ticket**
   - Just implement 3 methods
   - Output any format we want
   - asciinema does the heavy lifting

4. **Rust is actually beneficial**
   - Type safety ensures correctness
   - Performance (important for real-time recording)
   - Modern tooling (cargo, clippy, rustfmt)

---

## 🔮 Future Considerations

### Possible asciinema Contribution

If our .devsession format proves valuable, we could:
1. Clean up implementation
2. Add documentation
3. Submit PR to asciinema
4. Benefit: "Powered by asciinema" credibility

### Alternative: RecCli-specific Fork

If we want more control:
1. Fork asciinema → `reccli-recorder`
2. Strip unnecessary features (upload, auth, etc.)
3. Add .devsession as primary format
4. Distribute as part of RecCli

**Decision**: Start with Option 1 (custom encoder), consider fork later if needed.

---

## 📚 Resources

- [asciinema GitHub](https://github.com/asciinema/asciinema)
- [Rust Book](https://doc.rust-lang.org/book/)
- [async-trait docs](https://docs.rs/async-trait/)
- [serde_json docs](https://docs.rs/serde_json/)

---

**Next Document**: DEVSESSION_ENCODER_IMPLEMENTATION.md (detailed code walkthrough)

**Last Updated**: 2025-11-01
**Status**: Phase 0 - Ready to implement encoder
