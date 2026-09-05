// api/canvas.js — Vercel Serverless Function
// Fetches Canvas iCal feed using server-side environment variable.
// Protects the user's private Canvas calendar token from being exposed in public client code.

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  res.setHeader('Cache-Control', 'public, max-age=900, s-maxage=900');

  const icalUrl = process.env.CANVAS_ICAL_URL;
  if (!icalUrl) {
    return res.status(500).json({ error: 'CANVAS_ICAL_URL environment variable is not configured' });
  }

  try {
    const resp = await fetch(icalUrl, {
      headers: { 'User-Agent': 'PersonalDashboard-CanvasProxy' }
    });
    if (!resp.ok) {
      return res.status(resp.status).json({ error: `Canvas iCal fetch failed: ${resp.statusText}` });
    }
    const text = await resp.text();
    res.setHeader('Content-Type', 'text/calendar; charset=utf-8');
    return res.status(200).send(text);
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
