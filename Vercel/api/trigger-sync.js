// api/trigger-sync.js — Vercel Serverless Function
// Trigger GitHub Actions workflow dispatch via an external webhook (e.g. cron-job.org).
// Bypasses GitHub Actions cron queue delays and enforces strict, reliable execution intervals.

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  // Optional shared secret protection via query (?secret=...) or Bearer header
  const expectedSecret = process.env.CRON_SECRET;
  const providedSecret = req.query.secret || (req.headers.authorization ? req.headers.authorization.replace(/^Bearer\s+/i, '') : null);

  if (expectedSecret && providedSecret !== expectedSecret) {
    return res.status(401).json({ error: 'Unauthorized: invalid or missing secret' });
  }

  const token = process.env.GITHUB_DISPATCH_TOKEN || process.env.GITHUB_TOKEN || process.env.GIST_TOKEN;
  if (!token) {
    return res.status(500).json({
      error: 'Server configuration error: GITHUB_DISPATCH_TOKEN or GITHUB_TOKEN not configured in Vercel environment variables'
    });
  }

  // Supported targets
  const target = (req.query.target || 'jobs').toLowerCase();
  const workflows = {
    jobs: 'jobs-sync.yml',
    calendar: 'calendar-sync.yml',
    gmail: 'gmail-sync.yml',
    applications: 'applications-agent.yml',
  };

  const targetsToRun = target === 'all' ? ['jobs', 'calendar', 'gmail'] : [target];

  const results = [];
  for (const t of targetsToRun) {
    const workflowFile = workflows[t];
    if (!workflowFile) {
      results.push({ target: t, error: `Unknown target: ${t}. Valid targets: jobs, calendar, gmail, applications, all` });
      continue;
    }

    try {
      const ghResp = await fetch(
        `https://api.github.com/repos/SushantGanji3/PersonalDashboard/actions/workflows/${workflowFile}/dispatches`,
        {
          method: 'POST',
          headers: {
            'Accept': 'application/vnd.github+json',
            'Authorization': `Bearer ${token}`,
            'User-Agent': 'PersonalDashboard-Vercel-SyncTrigger',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ ref: 'main' }),
        }
      );

      if (ghResp.status === 204) {
        results.push({ target: t, workflow: workflowFile, status: 'dispatched' });
      } else {
        const errorText = await ghResp.text();
        results.push({ target: t, workflow: workflowFile, status: 'failed', httpStatus: ghResp.status, error: errorText });
      }
    } catch (err) {
      results.push({ target: t, workflow: workflowFile, status: 'error', message: err.message });
    }
  }

  const anyFailed = results.some(r => r.status !== 'dispatched');
  const statusCode = anyFailed && results.every(r => r.status !== 'dispatched') ? 500 : 200;

  return res.status(statusCode).json({
    ok: !anyFailed,
    timestamp: new Date().toISOString(),
    results,
  });
}
