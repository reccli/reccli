# RecCli Next.js Components

This directory contains Next.js components and API routes for the RecCli web app.

## Files

- **nextjs_landing.js** - React component for the landing page
- **api_routes.js** - Stripe API endpoints for checkout, webhooks, and license validation
- **landing_page_v2.html** - Alternative landing page design (dark theme)
- **package.json** - Next.js dependencies
- **.env.local.template** - Environment variables template

## Setup

### 1. Create Next.js App

```bash
npx create-next-app@latest reccli-web
cd reccli-web
```

### 2. Install Dependencies

```bash
npm install
```

### 3. Configure Environment Variables

```bash
cp .env.local.template .env.local
# Edit .env.local with your actual keys
```

### 4. Add Components

- Copy `nextjs_landing.js` to `pages/index.js`
- Copy the API routes from `api_routes.js` to:
  - `pages/api/create-checkout.js`
  - `pages/api/webhook.js`
  - `pages/api/validate-license.js`

### 5. Configure Tailwind CSS (if using the React component)

The `nextjs_landing.js` component uses Tailwind CSS. Follow Next.js Tailwind setup:

```bash
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

Update `tailwind.config.js`:

```js
module.exports = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

### 6. Stripe Setup

1. Create a Stripe account at https://stripe.com
2. Get your API keys from the Stripe Dashboard
3. Create a product with a $5/month subscription
4. Set `trial_period_days: 7` in the product settings
5. Add the Price ID to your `.env.local`

### 7. Deploy

#### Vercel (Recommended)

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel
```

#### Manual Deploy

1. Build: `npm run build`
2. Start: `npm start`
3. Configure your server to serve on port 3000

## Webhook Setup

After deploying, configure your Stripe webhook:

1. Go to Stripe Dashboard → Developers → Webhooks
2. Add endpoint: `https://reccli.com/api/webhook`
3. Select events:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
4. Copy the webhook secret to `.env.local`

## Testing

### Test Stripe Checkout

```bash
# Use Stripe test mode keys
# Test card: 4242 4242 4242 4242
# Any future expiry date and CVC
```

### Test License Validation

```bash
curl -X POST https://reccli.com/api/validate-license \
  -H "Content-Type: application/json" \
  -d '{"license_key":"reccli_test123","device_id":"test-device"}'
```

## Production Checklist

- [ ] Switch to Stripe live keys
- [ ] Set up Supabase for license storage
- [ ] Configure email service (Resend/SendGrid)
- [ ] Add analytics (Plausible/Google Analytics)
- [ ] Test checkout flow end-to-end
- [ ] Test webhook events
- [ ] Set up monitoring (Sentry, LogRocket)
- [ ] Configure custom domain
- [ ] Enable HTTPS
- [ ] Test on mobile devices

## License

Proprietary - See LICENSE file in the root directory.
