// api/gmail.js — Vercel Serverless Function
// Real-time live Gmail fetcher for PersonalDashboard on Vercel.
// If GMAIL_TOKEN is configured in Vercel Environment Variables, calls Gmail API directly in ~300ms.
// Otherwise falls back immediately to the GitHub Gist via GitHub API (bypassing edge CDN caching).

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0');

  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const gmailTokenB64 = process.env.GMAIL_TOKEN;

  // 1. Direct real-time Gmail API fetch (identical to Claude Artifact speed)
  if (gmailTokenB64) {
    try {
      const tokenJson = JSON.parse(Buffer.from(gmailTokenB64, 'base64').toString('utf-8'));
      const clientId = tokenJson.client_id;
      const clientSecret = tokenJson.client_secret;
      const refreshToken = tokenJson.refresh_token;

      if (clientId && clientSecret && refreshToken) {
        // Exchange refresh token for fresh access token
        const tokenResp = await fetch('https://oauth2.googleapis.com/token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            client_id: clientId,
            client_secret: clientSecret,
            refresh_token: refreshToken,
            grant_type: 'refresh_token',
          }),
        });

        if (tokenResp.ok) {
          const { access_token } = await tokenResp.json();

          // Query recent inbox threads (matches Claude Artifact: newer_than:2d in:inbox)
          const listResp = await fetch(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages?q=in:inbox%20newer_than:2d&maxResults=40',
            { headers: { Authorization: `Bearer ${access_token}` } }
          );

          if (listResp.ok) {
            const listData = await listResp.json();
            const stubs = listData.messages || [];

            // Fetch metadata in parallel
            const threadMap = new Map();
            await Promise.all(
              stubs.slice(0, 30).map(async (stub) => {
                try {
                  const msgResp = await fetch(
                    `https://gmail.googleapis.com/gmail/v1/users/me/messages/${stub.id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`,
                    { headers: { Authorization: `Bearer ${access_token}` } }
                  );
                  if (!msgResp.ok) return;
                  const msg = await msgResp.json();
                  const headers = {};
                  for (const h of (msg.payload?.headers || [])) {
                    headers[h.name.toLowerCase()] = h.value;
                  }

                  const threadId = msg.threadId || stub.id;
                  let dateIso = null;
                  try {
                    dateIso = headers.date ? new Date(headers.date).toISOString() : null;
                  } catch (_) {}

                  const entry = {
                    id: threadId,
                    messages: [{
                      sender: headers.from || 'Unknown sender',
                      subject: headers.subject || '(no subject)',
                      date: dateIso,
                      labelIds: msg.labelIds || [],
                    }],
                  };

                  const existing = threadMap.get(threadId);
                  const existingDate = existing?.messages?.[0]?.date || '';
                  if (!existing || (dateIso && dateIso > existingDate)) {
                    threadMap.set(threadId, entry);
                  }
                } catch (_) {}
              })
            );

            const threads = sortThreadsDescending(Array.from(threadMap.values()));
            return res.status(200).json({
              generatedAt: new Date().toISOString(),
              source: 'live_gmail_api',
              threads,
            });
          }
        }
      }
    } catch (e) {
      console.error('Error in direct Gmail API fetch:', e);
    }
  }

  // 2. Fallback: GitHub Gists API (fresh, bypasses raw CDN cache)
  try {
    const gistResp = await fetch('https://api.github.com/gists/0155252da48c020d0fadb9fdc5e43c2d', {
      headers: { 'User-Agent': 'PersonalDashboard-Vercel' },
    });
    if (gistResp.ok) {
      const gistData = await gistResp.json();
      const rawContent = gistData.files?.['gmail_inbox.json']?.content;
      if (rawContent) {
        const parsed = JSON.parse(rawContent);
        parsed.source = 'gist_api';
        if (Array.isArray(parsed.threads)) {
          parsed.threads = sortThreadsDescending(parsed.threads);
        }
        return res.status(200).json(parsed);
      }
    }
  } catch (err) {
    console.error('Gist API fetch error:', err);
  }

  // 3. Final raw Gist fallback
  try {
    const rawResp = await fetch(
      'https://gist.githubusercontent.com/SushantGanji3/0155252da48c020d0fadb9fdc5e43c2d/raw/gmail_inbox.json?t=' + Date.now(),
      { cache: 'no-store' }
    );
    const data = await rawResp.json();
    data.source = 'gist_raw';
    if (Array.isArray(data.threads)) {
      data.threads = sortThreadsDescending(data.threads);
    }
    return res.status(200).json(data);
  } catch (err) {
    return res.status(500).json({ error: 'Failed to fetch emails', message: err.message });
  }
}

function sortThreadsDescending(threads) {
  if (!Array.isArray(threads)) return [];
  return threads.slice().sort((a, b) => {
    const getThreadTime = (t) => {
      const msgs = t?.messages || [];
      let maxTime = 0;
      for (const m of msgs) {
        if (m?.date) {
          const tm = new Date(m.date).getTime();
          if (!isNaN(tm) && tm > maxTime) maxTime = tm;
        }
      }
      return maxTime;
    };
    return getThreadTime(b) - getThreadTime(a);
  });
}
