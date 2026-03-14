// pages/index.tsx
import Head from 'next/head'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import type { NextPage } from 'next'

interface Stats {
  recordings: number
  hours: number
  users: number
  retention: number
}

interface FAQ {
  q: string
  a: string
}

const RecCliLandingPage: NextPage = () => {
  const [stats, setStats] = useState<Stats>({
    recordings: 0,
    hours: 0,
    users: 0,
    retention: 0
  })

  const [copied, setCopied] = useState<boolean>(false)
  const [faqOpen, setFaqOpen] = useState<Record<number, boolean>>({})

  useEffect(() => {
    // Animate stats on mount
    const timer = setTimeout(() => {
      setStats({
        recordings: 12847,
        hours: 3421,
        users: 892,
        retention: 98
      })
    }, 500)

    return () => clearTimeout(timer)
  }, [])

  const copyInstall = (): void => {
    navigator.clipboard.writeText('curl -sSL https://reccli.com/install.sh | bash')
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const toggleFaq = (index: number): void => {
    setFaqOpen(prev => ({ ...prev, [index]: !prev[index] }))
  }

  const faqs: FAQ[] = [
    {
      q: "Why not just use the script command?",
      a: "Same reason you don't use Print Screen for screenshots. Sure, it works, but CleanShot X is a $30M business because reducing friction changes behavior. You'll never remember to type 'script' before that debugging session. You will click a red button that's always visible."
    },
    {
      q: "How is this different from script or manual terminal logs?",
      a: "RecCli uses native recording and session capture, but the bigger difference is workflow. The point is to make recording and continuity easy enough that you actually use it when the important debugging session happens."
    },
    {
      q: "Why $5/month with no free tier?",
      a: "Because free users don't value tools. They use them until they break, then complain. Paying users value tools. They use them properly and give feedback. $5 is less than your coffee. If reccli saves you one debugging session, it paid for itself for months."
    },
    {
      q: "Where are recordings stored?",
      a: "Locally on your machine in ~/reccli/sessions/. RecCli keeps session data local by default."
    }
  ]
  
  return (
    <>
      <Head>
        <title>reccli - Never Lose Terminal Context Again</title>
        <meta name="description" content="One-click terminal recording. Because the best debugging session is the one you actually recorded." />
        <meta property="og:title" content="reccli - One-Click Terminal Recording" />
        <meta property="og:description" content="Stop losing debugging gold. A floating record button for your terminal. $5/mo." />
        <meta property="og:image" content="https://reccli.com/og-image.png" />
        <meta property="og:url" content="https://reccli.com" />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:creator" content="@reccli_app" />
        <link rel="icon" href="/favicon.ico" />
      </Head>
      
      <div className="min-h-screen text-white" style={{ background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)' }}>
        {/* Gradient Background */}
        <div className="fixed inset-0 pointer-events-none" />
        
        {/* Header */}
        <header className="top-0 w-full z-50 backdrop-blur-lg" style={{ background: 'rgba(255,255,255,0.1)' }}>
          <nav className="container mx-auto px-6 py-4 flex justify-between items-center">
            <div className="flex items-center gap-3">
              <div className="w-3 h-3 bg-red-500 rounded-full" />
              <span className="text-2xl font-bold">reccli</span>
            </div>
            <a href="https://github.com/willluecke/RecCli" className="px-6 py-2 backdrop-blur-lg rounded-lg font-semibold hover:scale-105 transition-transform flex items-center gap-2" style={{ background: 'rgba(255,255,255,0.2)' }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
              </svg>
              GitHub
            </a>
          </nav>
        </header>
        
        {/* Hero Section */}
        <section className="pt-32 pb-20 px-6 relative overflow-hidden">
          {/* Background Image */}
          <div
            className="absolute inset-0 opacity-30"
            style={{
              backgroundImage: 'url(/datatothought.png)',
              backgroundSize: 'cover',
              backgroundPosition: 'center',
              backgroundRepeat: 'no-repeat',
              filter: 'blur(2px)'
            }}
          />

          {/* Content */}
          <div className="container mx-auto max-w-5xl text-center relative z-10">
            <h1 className="text-5xl md:text-7xl font-black mb-6 leading-tight">
              Never Lose Terminal Context Again
            </h1>

            <p className="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
              One-click CLI recording with a floating button that stays out of your way.
            </p>
            
            <div 
              className="bg-black/50 backdrop-blur-lg border border-white/10 rounded-lg p-6 mb-8 cursor-pointer hover:bg-black/70 transition-colors max-w-2xl mx-auto"
              onClick={copyInstall}
            >
              <span className="text-xs text-gray-500 float-right">
                {copied ? 'Copied!' : 'Click to copy'}
              </span>
              <code className="text-green-400 font-mono">
                curl -sSL https://reccli.com/install.sh | bash
              </code>
            </div>
            
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <Link href="/checkout" className="px-8 py-4 bg-gradient-to-r from-purple-600 to-pink-600 rounded-xl font-semibold text-lg hover:scale-105 transition-transform">
                Start 7-Day Free Trial
              </Link>
              <a 
                href="#how" 
                className="px-8 py-4 bg-white/10 backdrop-blur-lg rounded-xl font-semibold text-lg border-2 border-white/20 hover:bg-white/20 transition-colors"
              >
                See How It Works
              </a>
            </div>
          </div>
        </section>
        
        {/* Problem Section */}
        <section className="py-20 px-6">
          <div className="container mx-auto max-w-6xl">
            <h2 className="text-3xl md:text-4xl font-bold text-center mb-12">
              Yes, `script` exists. So does walking, but we still use cars.
            </h2>
            
            <div className="grid md:grid-cols-3 gap-8">
              <div className="bg-white/5 backdrop-blur-lg rounded-xl p-8 border border-white/10">
                <h3 className="text-xl font-bold text-red-400 mb-4">The "Hindsight" Problem</h3>
                <p className="text-gray-400 leading-relaxed">
                  That bug you just spent 2 hours fixing? You finally found it... but didn't record the session. 
                  Now you need to explain it to your team. Good luck.
                </p>
              </div>
              
              <div className="bg-white/5 backdrop-blur-lg rounded-xl p-8 border border-white/10">
                <h3 className="text-xl font-bold text-red-400 mb-4">The "Friction" Problem</h3>
                <p className="text-gray-400 leading-relaxed font-mono text-sm">
                  script -r session_$(date +%Y%m%d).log<br />
                  vs<br />
                  *click*<br /><br />
                  <span className="text-white">Which one will you actually use?</span>
                </p>
              </div>
              
              <div className="bg-white/5 backdrop-blur-lg rounded-xl p-8 border border-white/10">
                <h3 className="text-xl font-bold text-red-400 mb-4">The "Invisible" Problem</h3>
                <p className="text-gray-400 leading-relaxed">
                  Command-line tools are invisible until you need them. 
                  A floating button? You see it. You click it. You actually use it.
                </p>
              </div>
            </div>
          </div>
        </section>
        
        {/* Stats Section */}
        <section className="py-20 px-6 border-y border-white/10">
          <div className="container mx-auto max-w-6xl">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
              <div>
                <div className="text-4xl font-bold text-purple-400 transition-all duration-1000">
                  {stats.recordings.toLocaleString()}
                </div>
                <div className="text-gray-500 mt-2">Sessions Recorded</div>
              </div>
              <div>
                <div className="text-4xl font-bold text-purple-400 transition-all duration-1000">
                  {stats.hours.toLocaleString()}
                </div>
                <div className="text-gray-500 mt-2">Hours Captured</div>
              </div>
              <div>
                <div className="text-4xl font-bold text-purple-400 transition-all duration-1000">
                  {stats.users.toLocaleString()}
                </div>
                <div className="text-gray-500 mt-2">Active Users</div>
              </div>
              <div>
                <div className="text-4xl font-bold text-purple-400 transition-all duration-1000">
                  {stats.retention}%
                </div>
                <div className="text-gray-500 mt-2">Keep Using It</div>
              </div>
            </div>
          </div>
        </section>
        
        {/* Pricing Section */}
        <section className="py-20 px-6">
          <div className="container mx-auto max-w-md text-center">
            <h2 className="text-4xl font-bold mb-4">Simple Pricing. No BS.</h2>
            <p className="text-gray-400 mb-12">
              No free tier. Good tools cost money. This costs less than a coffee.
            </p>
            
            <div className="bg-gradient-to-br from-purple-600/20 to-pink-600/20 rounded-2xl p-10 border-2 border-purple-500/30">
              <div className="text-6xl font-black mb-2">$5</div>
              <div className="text-gray-400 mb-8">per month after 7-day trial</div>
              
              <ul className="text-left space-y-4 mb-10">
                {[
                  'Unlimited recordings',
                  'Floating record button',
                  'Perfect playback',
                  'Local storage (your data)',
                  'Share links (coming soon)',
                  'Cloud backup (coming soon)'
                ].map((feature, i) => (
                  <li key={i} className="flex items-center gap-3">
                    <div className="w-5 h-5 bg-green-500 rounded-full flex items-center justify-center flex-shrink-0">
                      <span className="text-xs">✓</span>
                    </div>
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
              
              <Link href="/checkout" className="block w-full px-8 py-4 bg-gradient-to-r from-purple-600 to-pink-600 rounded-xl font-semibold text-lg hover:scale-105 transition-transform">
                Start 7-Day Free Trial
              </Link>
              
              <p className="text-sm text-gray-500 mt-6">
                Cancel anytime. No questions asked.
              </p>
            </div>
          </div>
        </section>
        
        {/* FAQ Section */}
        <section className="py-20 px-6">
          <div className="container mx-auto max-w-3xl">
            <h2 className="text-4xl font-bold text-center mb-12">
              Questions You're Thinking
            </h2>
            
            <div className="space-y-4">
              {faqs.map((faq, i) => (
                <div key={i} className="border border-white/10 rounded-lg overflow-hidden">
                  <button
                    className="w-full px-6 py-4 bg-white/5 hover:bg-white/10 transition-colors flex justify-between items-center text-left"
                    onClick={() => toggleFaq(i)}
                  >
                    <span className="font-semibold">{faq.q}</span>
                    <span className="text-gray-400">{faqOpen[i] ? '−' : '+'}</span>
                  </button>
                  {faqOpen[i] && (
                    <div className="px-6 py-4 text-gray-400 leading-relaxed">
                      {faq.a}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>
        
        {/* Footer */}
        <footer className="py-12 px-6 text-center text-gray-500">
          <p>© 2024 reccli. Made by developers, for developers.</p>
          <p className="mt-2">
            <a href="mailto:hello@reccli.com" className="hover:text-white">hello@reccli.com</a>
            {' · '}
            <a href="https://twitter.com/reccli_app" className="hover:text-white">@reccli_app</a>
          </p>
        </footer>
      </div>
    </>
  )
}

export default RecCliLandingPage
