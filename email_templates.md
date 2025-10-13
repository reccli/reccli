# reccli Email Templates

## 1. Welcome Email (Immediately after signup)

**Subject:** 🎬 Welcome to reccli! Here's your license key

```
Hey there!

Welcome to reccli - you just made debugging 10x easier.

Your license key: {{LICENSE_KEY}}

## Quick Start (30 seconds):

1. Install reccli if you haven't:
   curl -sSL https://reccli.com/install.sh | bash

2. Activate your license:
   reccli activate {{LICENSE_KEY}}

3. Start the GUI:
   reccli gui

That's it! Click the floating button to start/stop recording.

## Your 7-day trial includes:
✅ Unlimited recordings
✅ Full feature access
✅ No watermarks or limits

## Pro tips:
• Drag the button anywhere on screen
• Press Space to start/stop (when button is focused)
• Right-click for quick menu
• Use `reccli stats` to see your recording stats

You won't be charged until {{TRIAL_END_DATE}}.
Cancel anytime at: https://reccli.com/cancel

Need help? Just reply to this email.

Happy recording!
The reccli team

P.S. Share your first recording on X with #reccli - we'd love to see what you're building!
```

---

## 2. Day 3 Check-in

**Subject:** Quick tip: Did you know reccli can do this?

```
Hey!

You've been using reccli for 3 days now. Here's a power feature you might have missed:

🎯 **Instant replay:** After catching a bug, use `reccli play` to replay your last session.

Perfect for:
• Sharing exact reproduction steps with your team
• Creating bug reports that actually make sense
• Reviewing what commands you ran yesterday

Your trial has 4 days left. Getting value from reccli?
Tweet about it: https://twitter.com/intent/tweet?text=Been%20using%20@reccli%20for%203%20days%20-%20already%20saved%20me%20hours%20of%20debugging%20time!

Questions? Just reply!
```

---

## 3. Day 6 Reminder

**Subject:** Your reccli trial ends tomorrow

```
Hey there,

Quick reminder: Your reccli trial ends tomorrow ({{TRIAL_END_DATE}}).

## Your stats so far:
📼 {{RECORDINGS_COUNT}} sessions recorded
⏱️ {{HOURS_SAVED}} hours of debugging captured
🔥 {{STREAK_DAYS}} day recording streak

After tomorrow, you'll automatically continue at just $5/month.

**Don't want to continue?** No worries!
Cancel here: https://reccli.com/cancel?token={{CANCEL_TOKEN}}

**Want to keep your recordings safe?**
You're all set - no action needed.

Thanks for trying reccli. Whichever way you decide, your recordings stay on your machine forever.

Best,
The reccli team

P.S. As a thank you for being an early user, reply "EARLYBIRD" and we'll lock you in at $3/month forever (40% off).
```

---

## 4. Post-Trial Conversion (Day 8)

**Subject:** ✅ You're all set with reccli!

```
Welcome to the reccli community! 🎉

Your subscription is now active. Here's what's new:

## Coming this month:
• **Cloud backup** - Never lose a recording
• **Team sharing** - Send recording links to teammates
• **AI summaries** - Auto-generate command sequences

## Your account:
• Billing: $5/month
• Next charge: {{NEXT_BILLING_DATE}}
• Manage subscription: https://reccli.com/account

## Quick wins:
Try these workflows that other devs love:

1. **Debug diary:** Record every debugging session. Review what worked later.

2. **Onboarding videos:** Record your dev environment setup. Perfect for new hires.

3. **Proof of work:** Record fixing bugs. Great for standups and reviews.

Share your favorite reccli moment on X - we'll retweet the best ones!

Happy recording,
The reccli team
```

---

## 5. Cancellation Prevention (When user clicks cancel)

**Subject:** Before you go - did something break?

```
Hey!

I see you're thinking about cancelling reccli.

Before you go, can you help me understand what went wrong?

Reply with a number:
1. Too expensive
2. Not using it enough  
3. Missing a feature I need
4. Technical issues
5. Found an alternative
6. Other

If it's something we can fix, I'd love to make it right.

And if you just need a break, here's a secret link for 2 months free:
https://reccli.com/pause?token={{PAUSE_TOKEN}}

Either way, thanks for trying reccli. Your recordings will always stay on your machine.

Best,
Alex (founder of reccli)

P.S. Seriously, just reply with a number. I read every response personally.
```

---

## 6. Win-back Email (30 days after cancellation)

**Subject:** We added the feature you wanted 👀

```
Hey!

Quick update: Remember that feature you wanted in reccli?

We just shipped:
✨ Cloud sync
✨ Team sharing  
✨ Search across all recordings
✨ 2x faster playback

Want to give it another shot?

Here's 50% off your first 3 months:
https://reccli.com/comeback?code=MISSYOU50

No pressure - just wanted to let you know!

Best,
The reccli team

P.S. Your old recordings are still on your machine. The new version works with them perfectly.
```

---

## Email Timing Strategy:

| Email | Timing | Purpose | CTA |
|-------|--------|---------|-----|
| Welcome | Instant | Onboard + activate | Install & activate |
| Check-in | Day 3 | Show value | Share on social |
| Reminder | Day 6 | Prevent surprise billing | Cancel or continue |
| Conversion | Day 8 | Celebrate + upsell | Share success |
| Cancel prevention | On cancel click | Save the sale | Pause or feedback |
| Win-back | Day 30 post-cancel | Re-engage | 50% discount |

---

## A/B Test Variants:

**Subject lines to test:**
- "Your terminal has a record button now 🔴"
- "{{FIRST_NAME}}, you've recorded {{COUNT}} sessions!"
- "1 day left in your reccli trial"
- "The tool every dev will have by 2025"

**Pricing anchors to test:**
- "$5/month (less than a coffee)"
- "$60/year (save 2 months)"
- "$5/month (97% cheaper than losing one debugging session)"
