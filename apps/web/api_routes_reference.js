// pages/api/create-checkout.js
import Stripe from 'stripe';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { email, device_id } = req.body;

    // Create or get customer
    let customer;
    const customers = await stripe.customers.list({
      email,
      limit: 1
    });

    if (customers.data.length > 0) {
      customer = customers.data[0];
    } else {
      customer = await stripe.customers.create({
        email,
        metadata: {
          device_id
        }
      });
    }

    // Create checkout session
    const session = await stripe.checkout.sessions.create({
      customer: customer.id,
      payment_method_types: ['card'],
      line_items: [
        {
          price: process.env.STRIPE_PRICE_ID, // Your price ID from Stripe
          quantity: 1,
        },
      ],
      mode: 'subscription',
      subscription_data: {
        trial_period_days: 7,
        metadata: {
          device_id
        }
      },
      success_url: `${process.env.NEXT_PUBLIC_BASE_URL}/success?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${process.env.NEXT_PUBLIC_BASE_URL}/`,
      metadata: {
        device_id
      }
    });

    res.status(200).json({ 
      checkout_url: session.url,
      session_id: session.id 
    });
  } catch (error) {
    console.error('Stripe error:', error);
    res.status(500).json({ 
      error: error.message 
    });
  }
}

// pages/api/webhook.js
import Stripe from 'stripe';
import { buffer } from 'micro';

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET;

export const config = {
  api: {
    bodyParser: false,
  },
};

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const buf = await buffer(req);
  const sig = req.headers['stripe-signature'];

  let event;

  try {
    event = stripe.webhooks.constructEvent(buf, sig, webhookSecret);
  } catch (err) {
    console.error('Webhook signature verification failed:', err.message);
    return res.status(400).send(`Webhook Error: ${err.message}`);
  }

  // Handle the event
  switch (event.type) {
    case 'checkout.session.completed':
      const session = event.data.object;
      
      // Generate license key
      const licenseKey = `reccli_${Math.random().toString(36).substring(2, 15)}`;
      
      // TODO: Store in database
      console.log('New subscription:', {
        customer: session.customer,
        subscription: session.subscription,
        device_id: session.metadata.device_id,
        license_key: licenseKey
      });
      
      // TODO: Send welcome email with license key
      
      break;
      
    case 'customer.subscription.updated':
      const subscription = event.data.object;
      console.log('Subscription updated:', subscription.id, subscription.status);
      // TODO: Update database
      break;
      
    case 'customer.subscription.deleted':
      const canceledSub = event.data.object;
      console.log('Subscription canceled:', canceledSub.id);
      // TODO: Update database, send cancellation email
      break;
      
    default:
      console.log(`Unhandled event type ${event.type}`);
  }

  res.status(200).json({ received: true });
}

// pages/api/validate-license.js
export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { license_key, device_id } = req.body;

  // TODO: Check database for valid license
  // For now, simple validation
  if (license_key && license_key.startsWith('reccli_')) {
    res.status(200).json({
      valid: true,
      status: 'active',
      expires_at: null
    });
  } else {
    res.status(401).json({
      valid: false,
      status: 'invalid',
      message: 'Invalid license key'
    });
  }
}