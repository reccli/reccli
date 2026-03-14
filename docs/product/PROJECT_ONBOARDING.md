# Project Onboarding & Scoping

**Status:** Product/design document for an optional future onboarding flow.

The current RecCli CLI does not implement `.devproject` onboarding. This document describes one possible future flow for creating a project-outline file, not a required first-run path. The intended product behavior is that RecCli can start working from `.devsession` alone and add `.devproject` later if the user wants a project-level outline.

## Overview

Conversational onboarding uses an **AI-guided project scoping interview** to capture intent, goals, and requirements that can't be inferred from code alone.

This creates a strong foundation for an optional `.devproject` file with strategic context (purpose, users, goals) that code scanning can't reveal.

## When Onboarding is Triggered

Conversational onboarding is used in two scenarios:

### 1. Empty Projects (Automatic)
When RecCli detects a project with **no code files** (< 3 files excluding README, LICENSE, .git), onboarding is the clearest path because there is little existing project evidence to infer from:
```
Reason: No code to scan, so capture intent upfront
Flow: Conversational interview → Creates .devproject → User starts coding
Benefit: AI has full strategic context from first line of code
```

### 2. Existing Codebases (User Choice)
When RecCli detects a project with **existing code**, the preferred product behavior is that RecCli can still start immediately, then let the user choose whether to add project-level structure:
- **Option A:** Scan codebase (fast, technical context only)
- **Option B:** Answer questions (slower, strategic context too) ← Onboarding
- **Option C:** Minimal setup (skip both and start recording)

```
Reason: User wants business/product context captured, not just tech stack
Flow: Quick tech scan (pre-fill) → Conversational interview → Creates .devproject
Benefit: .devproject has both technical AND strategic context
```

**Key Insight:** For existing codebases, onboarding can **pre-fill** technical details from a quick scan, then focus questions on business context (purpose, users, goals, metrics) that code can't reveal.

---

## Why This Matters

**Problem:** If we only track code sessions, we miss:
- Why the project exists (value proposition)
- Who it's for (target users)
- What success looks like (goals, metrics)
- Strategic decisions (not just technical ones)

**Solution:** When useful, ask for strategic context in natural conversation, store it in `.devproject`, and reference it throughout development. But do not make that a prerequisite for using RecCli on an existing repo.

---

## Onboarding Trigger

**When to trigger:**
- ✅ RecCli starts in directory without .devproject
- ✅ User runs a future `reccli init`-style command
- ✅ User selects "Create New Project" from dropdown

**Skip if:**
- ❌ .devproject already exists
- ❌ User explicitly skips: "just start recording"
- ❌ RecCli already has enough session history to generate it later

---

## Three Complexity Levels

Users can choose how thorough they want to be:

### Quick Start (30 seconds)
Minimal questions, get started fast

### Standard Setup (2 minutes)
Balanced approach, captures core context

### Comprehensive (5 minutes)
Full project scoping, best for serious projects

---

## Quick Start Flow (30 seconds)

```
🤖 RecCli: Welcome! Let's set up your project.

   [ Quick Start ] [ Standard Setup ] [ Comprehensive ] [ Skip ]

---

🤖: What are you building?
👤: A CLI tool for recording terminal sessions

🤖: What problem does it solve?
👤: Developers lose AI context between sessions

🤖: What platform?
👤: CLI tool

---

✅ Project initialized! Starting recording...

[Minimal .devproject created]
```

**Generated .devproject:**
```json
{
  "project": {
    "name": "RecCli",
    "description": "CLI tool for recording terminal sessions",
    "purpose": "Developers lose AI context between sessions",
    "value_proposition": "Preserves context for seamless AI coding continuity",
    "status": "early_development"
  },
  "tech_stack": {
    "platform": "cli"
  }
}
```

---

## Standard Setup Flow (2 minutes)

```
🤖 RecCli: Let's set up your project! I'll ask a few questions to understand what you're building.

   This helps me:
   • Make better suggestions during development
   • Keep documentation up to date
   • Stay focused on your goals

   [ Standard Setup ] ← You chose this
   (You can change your answers later)

---

📋 PROJECT BASICS

🤖: What are you building? (1-2 sentences)
👤: A CLI tool that records terminal sessions and uses AI to maintain context
    across sessions

🤖: What problem does this solve? Who is it for?
👤: Developers using AI coding assistants (Claude Code, Cursor, etc.) lose context
    when sessions end. This preserves and intelligently loads context.

🤖: What's your core value proposition? Why would someone choose this over alternatives?
👤: Automatic context preservation without manual note-taking. Smart vector search
    finds relevant past context.

---

📱 PLATFORM & FORMAT

🤖: What type of project is this?

   [ ] Web application (React, Vue, Angular, etc.)
   [ ] Mobile app (iOS, Android, React Native, Flutter)
   [ ] Desktop application (Electron, Tauri, native)
   [X] CLI tool / Terminal application
   [ ] Library / Package / SDK
   [ ] API / Backend service
   [ ] Data pipeline / ETL
   [ ] Browser extension
   [ ] Other: ___________

---

⚙️ CORE FEATURES

🤖: What are your core features? (List 3-5 main features you plan to build)

👤: [AI helps format as bullet list]
   • Record terminal sessions (native PTY/WAL recording)
   • AI-powered session summarization
   • Smart context loading with vector search
   • .devsession format for portable context
   • Project overview that auto-updates

---

🎯 DEVELOPMENT STAGE

🤖: Where are you in development?

   [ ] Concept / Planning
   [X] Early Development (building MVP)
   [ ] MVP Complete
   [ ] Beta / Testing
   [ ] Production / Launched
   [ ] Maintenance Mode

---

🔧 TECH STACK

🤖: I see this is a Python project. Any other tech requirements?

   Auto-detected:
   • Language: Python
   • Dependencies: [scans package.json, requirements.txt, etc.]

   Additional details?
👤: Needs to integrate with Anthropic API, uses sentence-transformers for embeddings

[AI updates tech_stack automatically]

---

🎓 DEVELOPMENT APPROACH

🤖: A few final questions:

   Open source or private?
   [X] Open Source  [ ] Private  [ ] Unsure

   License? (if open source)
   [X] MIT  [ ] Apache  [ ] GPL  [ ] Other: ___

   Solo or team?
   [X] Solo  [ ] Small team (2-5)  [ ] Larger team

---

✅ SUMMARY

🤖: Here's what I captured:

   📦 Project: RecCli
   🎯 Purpose: Preserve AI context across terminal sessions
   💡 Value: Automatic context management with vector search
   🔨 Platform: CLI tool (Python)
   📊 Stage: Early Development
   🎫 License: MIT (Open Source)

   Look good? [Yes, create project] [Edit] [Start over]

---

✅ Project initialized! Your .devproject file is ready.

💡 Tip: I'll update this automatically as we work, but you can always edit it manually.

🚀 Ready to start coding? Click [● REC] to begin!
```

**Generated .devproject:**
```json
{
  "format": "devproject",
  "version": "1.0.0",
  "created_at": "2024-10-29T10:30:00Z",
  "last_updated": "session-000",

  "project": {
    "name": "RecCli",
    "description": "CLI tool that records terminal sessions and uses AI to maintain context across sessions",
    "purpose": "Enable developers using AI coding assistants to preserve and intelligently load context between sessions",
    "value_proposition": "Automatic context preservation without manual note-taking, using smart vector search to find relevant past context",
    "repository": null,
    "license": "MIT",
    "status": "early_development",
    "visibility": "open_source"
  },

  "tech_stack": {
    "platform": "cli",
    "languages": ["Python"],
    "frameworks": [],
    "key_dependencies": ["anthropic", "sentence-transformers"],
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "llm_model": "claude-sonnet-4.5"
  },

  "core_features": [
    "Record terminal sessions (native PTY/WAL recording)",
    "AI-powered session summarization",
    "Smart context loading with vector search",
    ".devsession format for portable context",
    "Project overview that auto-updates"
  ],

  "target_users": [
    "Developers using AI coding assistants",
    "Teams wanting to preserve development context",
    "Solo developers working across multiple sessions"
  ],

  "development": {
    "current_stage": "early_development",
    "team_size": "solo",
    "started_at": "2024-10-29"
  },

  "architecture": {
    "overview": "To be documented as project evolves",
    "components": [],
    "key_patterns": []
  },

  "project_phases": {
    "current_phase": "MVP Development",
    "completed_phases": [],
    "next_milestones": []
  },

  "sessions": [],
  "statistics": {
    "total_sessions": 0,
    "total_duration_hours": 0
  }
}
```

---

## Comprehensive Flow (5 minutes)

Includes everything from Standard Setup, plus:

### Additional Questions:

**🎯 TARGET USERS & ICP**
```
🤖: Who is your ideal user?

   Demographics:
   - Role: [e.g., "Software developers"]
   - Experience level: [e.g., "Intermediate to senior"]
   - Company size: [e.g., "Startups to mid-size companies"]

   Pain points:
   - What frustrates them? [e.g., "Losing context in AI sessions"]
   - What have they tried? [e.g., "Manual note-taking, session exports"]
   - Why didn't those work? [e.g., "Too manual, doesn't integrate"]

   Use cases:
   - Primary: [e.g., "Continue AI coding session from yesterday"]
   - Secondary: [e.g., "Share context with team members"]
   - Tertiary: [e.g., "Audit development decisions"]
```

**📊 SUCCESS METRICS**
```
🤖: How will you measure success?

   User metrics:
   - [ ] Daily active users
   - [ ] Session continuations
   - [ ] Time saved
   - [ ] Other: ___

   Product metrics:
   - [ ] Feature adoption
   - [ ] Context accuracy
   - [ ] Export quality
   - [ ] Other: ___

   Business metrics:
   - [ ] GitHub stars
   - [ ] Downloads/installs
   - [ ] Revenue (if monetized)
   - [ ] Other: ___
```

**🗺️ ROADMAP & MILESTONES**
```
🤖: What are your major milestones?

   Short-term (1-3 months):
   👤:
   - MVP with basic recording
   - .devsession format v1.0
   - Claude Code integration

   Medium-term (3-6 months):
   👤:
   - Vector search implementation
   - Team collaboration features
   - VS Code extension

   Long-term (6-12 months):
   👤:
   - Multi-tool support (Cursor, etc.)
   - Cloud sync (optional)
   - Analytics dashboard
```

**🎨 DESIGN & UX**
```
🤖: Any design requirements or constraints?

   UI/UX:
   - Design philosophy: [e.g., "Minimal, unobtrusive"]
   - Key UX principles: [e.g., "Zero friction, automatic everything"]
   - Inspiration: [e.g., "Inspired by terminal-native session capture"]

   Branding:
   - Color scheme: [e.g., "Terminal-friendly (green/black)"]
   - Visual style: [e.g., "Technical, developer-focused"]
```

**🚀 GO-TO-MARKET**
```
🤖: How will you launch and grow?

   Launch strategy:
   - Where will you launch? [e.g., "Hacker News, Reddit r/programming"]
   - When? [e.g., "Q4 2024"]
   - Marketing angle: [e.g., "Open source Claude Code enhancement"]

   Growth channels:
   - [X] Content marketing (blog posts)
   - [X] GitHub/open source community
   - [X] Developer communities
   - [ ] Paid ads
   - [ ] Other: ___

   Monetization (if applicable):
   - [ ] Free forever
   - [X] Freemium (basic free, premium paid)
   - [ ] Open core
   - [ ] Sponsorships
   - [ ] Other: ___
```

---

## AI System Prompt for Onboarding

```
You are RecCli's project initialization assistant. Your job is to guide users through project scoping with a friendly, conversational interview.

TONE:
- Friendly but efficient
- Technical but accessible
- Encouraging without being cheesy
- Get to the point, don't over-explain

RULES:
1. Ask ONE question at a time
2. Allow natural language answers (you'll format/structure them)
3. If user gives vague answer, ask clarifying follow-up
4. Show progress (e.g., "3/7 questions")
5. Let user skip questions ("Not sure yet" is valid)
6. Adapt questions based on previous answers
7. Detect contradictions and clarify
8. At end, summarize and confirm

QUESTION FLOW:
- Start with open-ended (what/why/who)
- Move to structured (multiple choice, checkboxes)
- End with optional/advanced questions
- Always show estimated time remaining

FORMATTING:
- Use emojis sparingly for visual anchors (📋 🎯 ⚙️)
- Use checkboxes for selections
- Use bullet points for lists
- Keep it scannable

ADAPTATION:
If user says "I'm building a SaaS product":
  → Ask about ICP, pricing, go-to-market

If user says "I'm building an open source library":
  → Skip monetization, focus on community

If user says "I'm not sure yet":
  → Keep it minimal, mark fields as "to_be_determined"

EXAMPLE INTERACTION:

User: "I'm building a marketplace for developers"

You: "Interesting! What kind of marketplace? What are developers buying/selling?"

User: "They can sell code templates and components"

You: "Got it - so a marketplace for reusable code assets. Who's your ideal seller? Are these individual developers or companies?"

[Continue naturally based on answers]

OUTPUT FORMAT:
After all questions, generate complete .devproject JSON with all captured information.
```

---

## Implementation in RecCli

### Flow Integration

```python
def start_reccli():
    """Main entry point for RecCli"""

    project_dir = detect_project_root()
    devproject_path = project_dir / '.devproject'

    # Check if project initialized
    if not devproject_path.exists():
        # First time in this project
        show_onboarding_choice()
    else:
        # Load existing project
        load_project(devproject_path)
        show_continuation_dialog()

def show_onboarding_choice():
    """Let user choose onboarding complexity"""

    choice = show_dialog(
        title="New Project Detected",
        message="I don't see a .devproject file. Let's set up your project!",
        buttons=[
            "Quick Start (30 sec)",
            "Standard Setup (2 min)",
            "Comprehensive (5 min)",
            "Skip - Just Start Recording"
        ]
    )

    if choice == "Quick Start":
        run_quick_onboarding()
    elif choice == "Standard Setup":
        run_standard_onboarding()
    elif choice == "Comprehensive":
        run_comprehensive_onboarding()
    else:
        create_minimal_devproject()
        start_recording()

def run_standard_onboarding():
    """Run conversational AI onboarding"""

    # Open chat interface
    chat = AIChat(
        system_prompt=ONBOARDING_SYSTEM_PROMPT,
        initial_message="Let's set up your project! I'll ask a few questions..."
    )

    # User has conversation with AI
    conversation = chat.run_until_complete()

    # Extract structured data from conversation
    devproject = extract_devproject_from_conversation(conversation)

    # Show summary for confirmation
    if show_confirmation(devproject):
        save_devproject(devproject)
        start_recording()
    else:
        # User wants to edit
        run_standard_onboarding()  # Start over or edit

def extract_devproject_from_conversation(conversation):
    """
    Use AI to extract structured .devproject from conversational Q&A
    """
    extraction_prompt = f"""
    Extract a complete .devproject JSON from this onboarding conversation.

    Conversation:
    {conversation}

    Generate valid .devproject JSON with all fields populated from the conversation.
    Use "to_be_determined" for any fields not discussed.
    """

    response = ai_generate(extraction_prompt)
    devproject = json.loads(response)

    return devproject
```

### UI Mockup

```
┌─────────────────────────────────────────────────────────┐
│  RecCli - Project Setup                            [x]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  🎯 Let's set up your project!                         │
│                                                         │
│  I'll ask a few questions to understand what you're    │
│  building. This takes about 2 minutes.                 │
│                                                         │
│  ┌─────────────────────────────────────────────────┐  │
│  │ What are you building? (1-2 sentences)          │  │
│  │                                                  │  │
│  │ ▌                                                │  │
│  │                                                  │  │
│  └─────────────────────────────────────────────────┘  │
│                                                         │
│  Progress: 1/7 ████░░░░░░░░░░░░░░  ~2 min remaining   │
│                                                         │
│  [ Skip this question ]            [ Continue → ]     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Benefits of This Approach

### 1. Strong Foundation
- AI knows WHY you're building, not just WHAT
- Context from day 1
- Prevents scope drift

### 2. Better AI Suggestions
```
# Without onboarding:
User: "Add authentication"
AI: "I'll add a login form" [generic]

# With onboarding (AI knows it's a CLI tool):
User: "Add authentication"
AI: "Since this is a CLI tool, should we use API keys, OAuth tokens,
     or integrate with system keychain?" [contextual]
```

### 3. Living Documentation
- PRD starts from onboarding answers
- Roadmap built from milestones
- ICP captured upfront
- No guessing about business goals

### 4. Adaptive Questioning
```
If project_type == "SaaS":
    ask_about_monetization()
    ask_about_icp()
    ask_about_marketing()

elif project_type == "open_source_library":
    ask_about_community()
    ask_about_adoption_strategy()
    ask_about_documentation()

elif project_type == "internal_tool":
    ask_about_users()
    ask_about_deployment()
    ask_about_maintenance()
```

### 5. Natural Conversation
Not a form - feels like talking to a product manager:
- "What are you building?"
- "Why? What problem does it solve?"
- "Who's it for?"
- "What does success look like?"

### 6. Flexible Detail Level
- Quick Start: Bare minimum, start fast
- Standard: Good balance (recommended)
- Comprehensive: Full scoping for serious projects
- Skip: Power users who'll edit .devproject manually

---

## Example: Complete Onboarding Output

### User Input (via conversation):
```
What are you building?
→ "A marketplace for developers to buy and sell React components"

What problem does it solve?
→ "Developers waste time building common UI components. They'd rather buy
   high-quality pre-built components."

Who's it for?
→ "React developers at startups and small companies who move fast"

Platform?
→ Web application

Core features?
→ - Browse component library
  - Preview components live
  - Purchase and download
  - Seller dashboard
  - Review/rating system

Tech stack?
→ "Next.js, TypeScript, Stripe for payments"

Development stage?
→ "Early development - have designs, starting to code"

Monetization?
→ "Take 20% commission on sales"

...
```

### Generated .devproject:
```json
{
  "project": {
    "name": "ComponentMarket",
    "description": "Marketplace for developers to buy and sell React components",
    "purpose": "Help developers save time by purchasing high-quality pre-built UI components",
    "value_proposition": "Access to professional React components without building from scratch, vetted by community",
    "status": "early_development"
  },

  "target_users": [
    {
      "segment": "Buyers",
      "description": "React developers at startups and small companies",
      "pain_points": ["Building UI components is time-consuming", "Hard to find quality reusable components"],
      "use_cases": ["Quick prototyping", "Production-ready components", "Learning from examples"]
    },
    {
      "segment": "Sellers",
      "description": "Experienced React developers and component library creators",
      "pain_points": ["Hard to monetize open source work", "Limited distribution channels"],
      "use_cases": ["Monetize component libraries", "Build reputation", "Passive income"]
    }
  ],

  "tech_stack": {
    "platform": "web",
    "languages": ["TypeScript"],
    "frameworks": ["Next.js", "React"],
    "key_dependencies": ["stripe", "prisma", "tailwindcss"]
  },

  "core_features": [
    "Browse component library with search/filters",
    "Live component preview with code sandbox",
    "Purchase and instant download",
    "Seller dashboard with analytics",
    "Review and rating system",
    "Component versioning",
    "License management"
  ],

  "business_model": {
    "type": "marketplace",
    "monetization": "commission",
    "commission_rate": 20,
    "pricing_model": "per_component",
    "target_price_range": "$19-199 per component"
  },

  "success_metrics": {
    "user_metrics": ["Monthly active buyers", "Seller count", "Repeat purchase rate"],
    "product_metrics": ["Component quality score", "Preview-to-purchase conversion"],
    "business_metrics": ["GMV (Gross Merchandise Value)", "Commission revenue", "Average component price"]
  },

  "project_phases": {
    "current_phase": "MVP Development",
    "next_milestones": [
      {
        "milestone": "MVP Launch",
        "target": "Q1 2025",
        "description": "Basic marketplace with 20+ components from 5 sellers",
        "priority": "high",
        "success_criteria": ["10 paying customers", "5 active sellers", "Positive feedback"]
      }
    ]
  },

  "go_to_market": {
    "launch_channels": ["Product Hunt", "Hacker News", "React community", "Twitter/X"],
    "marketing_strategy": "Content marketing (component tutorials) + community building",
    "launch_date": "2025-01-15"
  }
}
```

Now the AI has FULL context for every decision:
- Knows it's a marketplace (not just "a website")
- Knows target users (React devs at startups)
- Knows business model (20% commission)
- Knows success metrics (GMV, conversion)
- Can make strategic suggestions aligned with goals

---

## Comparison: With vs Without Onboarding

### Without Onboarding:
```
Session 1:
User: "Create a homepage"
AI: [makes generic homepage, no context]

Session 5:
User: "Add payment"
AI: "What payment provider?" [has to ask]

Session 10:
User: "We need to launch soon"
AI: "What's the MVP scope?" [has to ask]
```

### With Onboarding:
```
Session 1:
User: "Create a homepage"
AI: "For the component marketplace homepage, I'll create a browse view with
     search, featured components, and seller highlights - aligned with your
     MVP goal of showcasing the library"

Session 5:
User: "Add payment"
AI: "I'll integrate Stripe with your 20% commission model. Should I set up
     Connect for seller payouts or manual transfers for MVP?"

Session 10:
User: "We need to launch soon"
AI: "Based on your Q1 2025 target, we have 8 weeks. We've completed the
     browse and preview features. Remaining for MVP: purchase flow, seller
     dashboard, and review system. That's achievable."
```

**The AI is strategic, not just tactical.**

---

## Recommendation

**Yes, absolutely add conversational onboarding!**

**MVP Implementation:**
1. Add "Standard Setup (2 min)" flow - 7 core questions
2. Extract to .devproject JSON
3. Show confirmation summary
4. Optional: Let user edit in UI before saving

**Why this is better than auto-generating docs:**
- Captures intent BEFORE code (not after)
- User provides context explicitly (not inferred)
- One-time setup (not ongoing maintenance burden)
- Natural conversation (not forms)

**This makes RecCli's AI genuinely strategic** - it knows your goals, users, and success metrics from the start, so every suggestion is aligned with where you're trying to go.

Should I create the detailed system prompts for each onboarding level (Quick/Standard/Comprehensive)?
