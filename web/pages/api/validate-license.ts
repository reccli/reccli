import type { NextApiRequest, NextApiResponse } from 'next'

interface ValidateLicenseRequest {
  license_key: string
  device_id: string
}

interface ValidateLicenseResponse {
  valid: boolean
  status: string
  expires_at?: Date | null
  message?: string
  error?: string
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<ValidateLicenseResponse>
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed', valid: false, status: 'error' });
  }

  const { license_key, device_id } = req.body as ValidateLicenseRequest;

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