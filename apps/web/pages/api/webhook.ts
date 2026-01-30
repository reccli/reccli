import type { NextApiRequest, NextApiResponse } from 'next'
import Stripe from 'stripe'
import { buffer } from 'micro'

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
  apiVersion: '2023-10-16'
})
const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET!

export const config = {
  api: {
    bodyParser: false,
  },
}

interface WebhookResponse {
  received?: boolean
  error?: string
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<WebhookResponse | string>
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const buf = await buffer(req);
  const sig = req.headers['stripe-signature'];

  if (!sig) {
    return res.status(400).send('No stripe signature found');
  }

  let event;

  try {
    event = stripe.webhooks.constructEvent(buf, sig as string, webhookSecret);
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : 'Unknown error';
    console.error('Webhook signature verification failed:', errorMessage);
    return res.status(400).send(`Webhook Error: ${errorMessage}`);
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
        device_id: session.metadata?.device_id || null,
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