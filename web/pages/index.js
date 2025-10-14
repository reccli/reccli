// pages/index.js
import Head from 'next/head'
import { useState, useEffect } from 'react'
import Link from 'next/link'

export default function Home() {
  const [stats, setStats] = useState({
    recordings: 0,
    hours: 0,
    users: 0,
    retention: 0
  })
  
  const [copied, setCopied] = useState(false)
  const [faqOpen, setFaqOpen] = useState({})
  
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
  
  const copyInstall = () => {
    navigator.clipboard.writeText('curl -sSL https://reccli.com/install.sh | bash')
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  
  const toggleFaq = (index) => {
    setFaqOpen(prev => ({ ...prev, [index]: !prev[index] }))
  }
  
  const faqs = [
    {
      q: "Why not just use the script command?",
      a: "Same reason you don't use Print Screen for screenshots. Sure, it works, but CleanShot X is a $30M business because reducing friction changes behavior. You'll never remember to type 'script' before that debugging session. You will click a red button that's always visible."
    },
    {
      q: "What about asciinema?",
      a: "asciinema is great! We actually use it under the hood. But it's still command-line based. reccli is about the UI/UX layer - the floating button that makes you actually USE recording instead of forgetting about it."
    },
    {
      q: "Why $5/month with no free tier?",
      a: "Because free users don't value tools. They use them until they break, then complain. Paying users value tools. They use them properly and give feedback. $5 is less than your coffee. If reccli saves you one debugging session, it paid for itself for months."
    },
    {
      q: "Where are recordings stored?",
      a: "Locally on your machine in ~/.reccli/recordings/. Your data, your control. Cloud backup is coming soon as an option, but will never be required."
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
      
      <div className="min-h-screen bg-gray-900 text-white">
        {/* Gradient Background */}
        <div className="fixed inset-0 bg-gradient-to-br from-purple-600/10 to-pink-600/10 pointer-events-none" />
        
        {/* Header */}
        <header className="fixed top-0 w-full z-50 bg-gray-900/80 backdrop-blur-lg border-b border-white/10">
          <nav className="container mx-auto px-6 py-4 flex justify-between items-center">
            <div className="flex items-center gap-3">
              <div className="w-3 h-3 bg-red-500 rounded-full animate-pulse" />
              <span className="text-2xl font-bold">reccli</span>
            </div>
            <Link href="/checkout" className="px-6 py-2 bg-gradient-to-r from-purple-600 to-pink-600 rounded-lg font-semibold hover:scale-105 transition-transform">
              Start Free Trial
            </Link>
          </nav>
        </header>
        
        {/* Hero Section */}
        <section className="pt-32 pb-20 px-6">
          <div className="container mx-auto max-w-5xl text-center">
            <div className="inline-block px-6 py-2 mb-8 bg-white/10 backdrop-blur-lg rounded-full text-sm border border-white/20">
              🚀 Launch Week - 40% off for first 100 users
            </div>
            
            <h1 className="text-5xl md:text-7xl font-black mb-6 bg-gradient-to-br from-white to-gray-400 bg-clip-text text-transparent leading-tight">
              The Debugging Session<br />
              You Didn't Record<br />
              Is The One You'll Need
            </h1>
            
            <p className="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
              A floating record button for your terminal. Because "just use script" doesn't work when you forgot to use script.
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