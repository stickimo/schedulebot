'use strict';

const { Telegraf }             = require('telegraf');
const Anthropic                = require('@anthropic-ai/sdk');
const { Dropbox, DropboxAuth } = require('dropbox');
const fetch                    = require('node-fetch');
const cron                     = require('node-cron');
const fs                       = require('fs');
const { exec }                 = require('child_process');
const os                       = require('os');
const path                     = require('path');

// ── Config ─────────────────────────────────────────────────────────────────
const BOT_TOKEN   = process.env.BOT_TOKEN;
const CHAT_ID     = process.env.CHAT_ID;
const DBX_KEY     = process.env.DROPBOX_APP_KEY;
const DBX_SECRET  = process.env.DROPBOX_APP_SECRET;
const DBX_REFRESH = process.env.DROPBOX_REFRESH_TOKEN;
const CLAUDE_KEY  = process.env.ANTHROPIC_API_KEY;

const ROOT = '/MET';

// ── Clients ────────────────────────────────────────────────────────────────
const bot    = new Telegraf(BOT_TOKEN);
const claude = new Anthropic({ apiKey: CLAUDE_KEY });

async function dbx() {
  const auth = new DropboxAuth({
    clientId: DBX_KEY, clientSecret: DBX_SECRET,
    refreshToken: DBX_REFRESH, fetch,
  });
  await auth.refreshAccessToken();
  return new Dropbox({ auth, fetch });
}

// ── Dropbox helpers ────────────────────────────────────────────────────────
async function uploadFile(d, path, buf, mode = 'overwrite') {
  await d.filesUpload({ path, contents: buf, mode: { '.tag': mode }, autorename: false, mute: true });
}

async function downloadFile(d, path) {
  try {
    const res = await d.filesDownload({ path });
    return res.result.fileBinary;
  } catch (e) {
    if (e?.status === 409 || e?.status === 404) return null;
    throw e;
  }
}

// ── Schedule storage ───────────────────────────────────────────────────────
const SCHEDULE_FILE    = 'schedule.json';
const SCHEDULE_DBX     = `${ROOT}/schedule.json`;
const SNAPSHOTS_FOLDER = `${ROOT}/Schedules`;
const CONFIRMED_DBX    = `${ROOT}/breaks_confirmed.json`;
const ACTIVITY_LOG_DBX = `${ROOT}/activity_log.json`;

function loadSchedule() {
  if (fs.existsSync(SCHEDULE_FILE)) {
    try { return JSON.parse(fs.readFileSync(SCHEDULE_FILE, 'utf8')); } catch {}
  }
  return {};
}

async function saveSchedule(d, schedule) {
  const json = JSON.stringify(schedule, null, 2);
  JSON.parse(json); // validate before writing
  fs.writeFileSync(SCHEDULE_FILE, json);
  await uploadFile(d, SCHEDULE_DBX, Buffer.from(json));
}

async function restoreSchedule(d) {
  try {
    const buf = await downloadFile(d, SCHEDULE_DBX);
    if (buf) {
      fs.writeFileSync(SCHEDULE_FILE, buf);
      console.log('Restored schedule.json from Dropbox.');
    }
  } catch { console.log('No schedule.json in Dropbox. Starting fresh.'); }
}

async function snapshotSchedule(d) {
  const schedule = loadSchedule();
  const date     = today();
  const snapPath = `${SNAPSHOTS_FOLDER}/schedule_${date}.json`;
  await uploadFile(d, snapPath, Buffer.from(JSON.stringify(schedule, null, 2)));
  console.log(`Snapshot saved: ${snapPath}`);
}

// ── Activity log ───────────────────────────────────────────────────────────
async function loadActivityLog(d) {
  try {
    const buf = await downloadFile(d, ACTIVITY_LOG_DBX);
    if (!buf) return [];
    const parsed = JSON.parse(buf.toString('utf8'));
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

// ── Break pipeline ─────────────────────────────────────────────────────────
function parseBreakSchedule(str) {
  // "(1)7, (3)28, (1)H" → [{ count:1, days:7 }, { count:3, days:28 }, { count:1, days:null }]
  const breaks = [];
  const re = /\((\d+)\)(\d+|H)/gi;
  let m;
  while ((m = re.exec(str)) !== null) {
    breaks.push({
      count: parseInt(m[1], 10),
      days:  m[2].toUpperCase() === 'H' ? null : parseInt(m[2], 10),
    });
  }
  return breaks;
}

function addDays(dateStr, days) {
  const d = new Date(dateStr + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function today() { return new Date().toISOString().slice(0, 10); }

async function loadConfirmedBreaks(d) {
  try {
    const buf = await downloadFile(d, CONFIRMED_DBX);
    if (!buf) return {};
    return JSON.parse(buf.toString('utf8'));
  } catch { return {}; }
}

async function getBreaksDue(d, targetDate = null) {
  const date      = targetDate || today();
  const log       = await loadActivityLog(d);
  const confirmed = await loadConfirmedBreaks(d);

  // Only look at concrete field notes from the last 35 days
  const cutoff    = addDays(date, -35);
  const concrete  = log.filter(e =>
    e.category === 'ConcreteTesting' &&
    e.type     === 'fieldnote' &&
    e.timestamp?.slice(0, 10) >= cutoff
  );

  const due = [];
  for (const entry of concrete) {
    try {
      const filePath = `${ROOT}/${entry.folder}/${entry.filename}`;
      const buf      = await downloadFile(d, filePath);
      if (!buf) continue;
      const text = buf.toString('utf8');

      const jobMatch   = text.match(/#job_(\S+)/i);
      const setMatch   = text.match(/Set Number[:\s]+(\S+)/i);
      const schedMatch = text.match(/Break Schedule[:\s]+(.+)/i);
      if (!jobMatch || !setMatch || !schedMatch) continue;

      const job    = jobMatch[1];
      const setNum = setMatch[1].replace(/[^a-zA-Z0-9]/g, '');
      const breaks = parseBreakSchedule(schedMatch[1]);
      const castDate = entry.timestamp.slice(0, 10);

      for (const b of breaks) {
        if (!b.days) continue; // skip holds
        const dueDate = addDays(castDate, b.days);
        if (dueDate !== date) continue;
        const key = `${job}_set${setNum}_${b.days}day`;
        if (confirmed[key]) continue;
        due.push({ job, setNum, days: b.days, count: b.count, castDate, dueDate, key });
      }
    } catch { continue; }
  }

  return due;
}

// ── Format helpers ─────────────────────────────────────────────────────────
function formatScheduleDay(entries, dateStr) {
  if (!entries || entries.length === 0) return `📅 ${dateStr}: Nothing scheduled.`;
  const lines = entries.map(e =>
    `  ${e.time || '?'} — ${e.job ? e.job + ' ' : ''}${e.description || ''}`
  );
  return `📅 ${dateStr}:\n${lines.join('\n')}`;
}

function formatBreaksDue(breaks) {
  if (breaks.length === 0) return '🧪 No cylinder breaks due today.';
  const lines = breaks.map(b =>
    `  • ${b.job} Set ${b.setNum} — ${b.count} cyl @ ${b.days}-day (cast ${b.castDate})`
  );
  return `🧪 Breaks due today:\n${lines.join('\n')}`;
}

// ── Morning brief ──────────────────────────────────────────────────────────
async function sendMorningBrief() {
  try {
    const d        = await dbx();
    const schedule = loadSchedule();
    const date     = today();
    const entries  = schedule[date] || [];
    const breaks   = await getBreaksDue(d, date);

    const msg = [
      `🌅 Good morning! Here's ${date}:`,
      '',
      formatScheduleDay(entries, date),
      '',
      formatBreaksDue(breaks),
    ].join('\n');

    await bot.telegram.sendMessage(CHAT_ID, msg);
  } catch (e) { console.error('Morning brief error:', e); }
}

// ── Evening snapshot + unconfirmed break alerts ────────────────────────────
async function sendEveningSnapshot() {
  try {
    const d      = await dbx();
    const breaks = await getBreaksDue(d, today());
    await snapshotSchedule(d);

    let msg = `📸 Schedule snapshot saved for ${today()}.`;
    if (breaks.length > 0) {
      const lines = breaks.map(b =>
        `  ⚠️ ${b.job} Set ${b.setNum} — ${b.count} cyl @ ${b.days}-day — not yet confirmed`
      );
      msg += `\n\n🚨 Unconfirmed breaks from today:\n${lines.join('\n')}`;
    }

    await bot.telegram.sendMessage(CHAT_ID, msg);
  } catch (e) { console.error('Evening snapshot error:', e); }
}

// ── Claude ─────────────────────────────────────────────────────────────────
async function askClaude(messages, maxTokens = 2048) {
  const res = await claude.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: maxTokens,
    messages,
  });
  return res.content[0].text.trim();
}

function stripFences(s) {
  return s.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/```\s*$/i, '').trim();
}

// ── Schedule query / update handler ───────────────────────────────────────
async function handleScheduleQuery(ctx, text) {
  try {
    await ctx.reply('🗓 On it...');
    const d        = await dbx();
    const schedule = loadSchedule();
    const date     = today();

    const prompt =
`You are a scheduling assistant for Caleb Fischer, a field geotechnical technician.
Today is ${date}.

Current schedule (JSON):
${JSON.stringify(schedule, null, 2)}

User request: "${text}"

You have two possible response types:

1. If the user is MODIFYING the schedule (adding, removing, moving, updating entries):
   Return ONLY: { "action": "update", "schedule": { ...full updated schedule... } }
   - Each date key is YYYY-MM-DD
   - Value is ALWAYS an array, even for a single entry: [{ "time": "HH:MM", "job": "26-606", "description": "what they're doing" }]
   - Prune dates older than 30 days from today when returning the updated schedule

2. If the user is QUERYING the schedule (asking what's on a day, range, week, etc.):
   Return ONLY: { "action": "reply", "message": "...your formatted plain-text response..." }

Return only valid JSON. No markdown fences, no explanation.

IMPORTANT: This bot CAN generate PDF documents. Do NOT tell the user you cannot generate PDFs — that is false. If they ask for a PDF, tell them to say "pdf" or "pdf this week's schedule" and it will be handled automatically.`;

    const raw    = await askClaude([{ role: 'user', content: prompt }], 2048);
    const result = JSON.parse(stripFences(raw));

    if (result.action === 'update') {
      await saveSchedule(d, result.schedule);
      await ctx.reply('✅ Schedule updated.');
    } else if (result.action === 'reply') {
      await ctx.reply(result.message);
    } else {
      await ctx.reply('⚠️ Could not process that request.');
    }
  } catch (e) {
    console.error('Schedule query error:', e);
    await ctx.reply(`⚠️ Something went wrong: ${e.message}`);
  }
}

// ── PDF generation ────────────────────────────────────────────────────────
const GENERIC_SCRIPT = path.join(__dirname, 'report_generic.py');

async function generatePDF(title, content, date, theme = null) {
  const tmp      = os.tmpdir();
  const jsonPath = path.join(tmp, `sched_${Date.now()}.json`);
  const pdfPath  = path.join(tmp, `sched_${Date.now()}.pdf`);
  fs.writeFileSync(jsonPath, JSON.stringify({ title, content, date, theme }));
  await new Promise((resolve, reject) => {
    const proc = exec(`python3 "${GENERIC_SCRIPT}" "${jsonPath}" "${pdfPath}"`);
    setTimeout(() => { proc.kill(); reject(new Error('PDF generation timed out.')); }, 30000);
    proc.on('close', code => {
      try { fs.unlinkSync(jsonPath); } catch {}
      if (code === 0) resolve();
      else reject(new Error(`PDF renderer exited with code ${code}`));
    });
  });
  return pdfPath;
}

function formatScheduleAsMarkdown(schedule, startDate, endDate) {
  const lines = [];
  const cur = new Date(startDate + 'T12:00:00Z');
  const end = new Date(endDate   + 'T12:00:00Z');
  while (cur <= end) {
    const key     = cur.toISOString().slice(0, 10);
    const raw     = schedule[key];
    const entries = Array.isArray(raw) ? raw : raw ? [raw] : [];
    lines.push(`## ${key}`);
    if (entries.length === 0) {
      lines.push('Nothing scheduled.');
    } else {
      entries.forEach(e =>
        lines.push(`- ${e.time || '?'} — ${e.job ? e.job + ' ' : ''}${e.description || ''}`)
      );
    }
    lines.push('');
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return lines.join('\n');
}

function formatBreaksAsMarkdown(breaks, date) {
  if (breaks.length === 0) return 'No cylinder breaks due.';
  const lines = [
    `## Cylinder Breaks Due — ${date}`,
    '',
    '| Job | Set | Cylinders | Break Day | Cast Date |',
    '|-----|-----|-----------|-----------|-----------|',
    ...breaks.map(b =>
      `| ${b.job} | ${b.setNum} | ${b.count} | ${b.days}-day | ${b.castDate} |`
    ),
  ];
  return lines.join('\n');
}

// ── Commands ───────────────────────────────────────────────────────────────
const HELP_TEXT =
`📋 MET SCHEDULE BOT

Just talk to me naturally:
"Add San Luis Valley Plaza Thursday 8am"
"What do I have next week?"
"Move Tuesday to Wednesday"
"Clear Friday"
"What breaks are due this week?"
"Show me Thursday through Monday"

/brief  — Morning brief right now
/reset  — Clear any stuck state
/help   — Show this message`;

bot.command('start', ctx => ctx.reply(HELP_TEXT));
bot.command('help',  ctx => ctx.reply(HELP_TEXT));

bot.command('brief', async ctx => {
  await sendMorningBrief();
});

bot.command('reset', ctx => ctx.reply('🔄 Reset. Ready.'));

// ── Message handler ────────────────────────────────────────────────────────
bot.on('message', async ctx => {
  const text  = ctx.message.text?.trim();
  if (!text || text.startsWith('/')) return;
  const lower = text.toLowerCase();

  // PDF request — Claude generates the full content based on the user's exact request
  if (/pdf|schedule.*pdf|pdf.*schedule|break.*pdf|pdf.*break|break.*log/i.test(lower)) {
    try {
      await ctx.reply('⚙️ Generating PDF...');
      const d        = await dbx();
      const schedule = loadSchedule();
      const date     = today();
      const breaks   = await getBreaksDue(d, date);

      const prompt =
`You are generating content for a PDF document for a field geotechnical technician.
Today is ${date}.

Current schedule (JSON):
${JSON.stringify(schedule, null, 2)}

Cylinder breaks due today:
${JSON.stringify(breaks, null, 2)}

User request: "${text}"

Generate the full PDF content as markdown. Honor any special requests exactly —
themes, motivational quotes, specific date ranges, formatting styles, whatever they ask for.
Use ## for section headers, | tables |, - bullets, and **bold** as needed.
Include day names with dates (e.g. "Monday March 23").

You can also control the visual theme via a theme object. All fields optional:
  header_bg (hex), header_text (hex), h2_bg (hex), h2_text (hex),
  h3_color (hex), accent (hex), body_color (hex), page_bg (hex or null),
  body_font ("Helvetica" | "Times-Roman" | "Courier"),
  row_alt_bg (hex), table_hdr_bg (hex)

Return only a JSON object: { "title": "short title", "content": "full markdown content", "theme": { ...or null } }
No explanation, no fences.`;

      const raw    = await askClaude([{ role: 'user', content: prompt }], 2048);
      const result = JSON.parse(stripFences(raw));
      const pdfPath = await generatePDF(result.title, result.content, date, result.theme || null);

      await ctx.replyWithDocument(
        { source: fs.readFileSync(pdfPath), filename: `MET_${result.title.replace(/\s+/g, '_')}_${date}.pdf` },
        { caption: '📄 Here you go.' }
      );
      try { fs.unlinkSync(pdfPath); } catch {}
    } catch (e) {
      await ctx.reply(`❌ PDF generation failed: ${e.message}`);
    }
    return;
  }

  await handleScheduleQuery(ctx, text);
});

// ── Cron jobs (Mountain Time) ──────────────────────────────────────────────
cron.schedule('0 6  * * *', sendMorningBrief,    { timezone: 'America/Denver' });
cron.schedule('0 16 * * *', sendEveningSnapshot, { timezone: 'America/Denver' });

// ── Boot ───────────────────────────────────────────────────────────────────
async function boot() {
  const d = await dbx();
  await restoreSchedule(d);
  console.log('Schedule restored.');
  try { await bot.telegram.deleteWebhook({ drop_pending_updates: true }); } catch {}
  await new Promise(r => setTimeout(r, 2000));
  bot.launch({ dropPendingUpdates: true }).then(() => console.log('MET ScheduleBot running.'));
  process.once('SIGINT',  () => bot.stop('SIGINT'));
  process.once('SIGTERM', () => bot.stop('SIGTERM'));
}

boot().catch(console.error);
