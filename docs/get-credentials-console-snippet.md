# Get Notion Credentials (Console Snippet)

The fastest way to obtain the three credentials this tool needs (`spaceId`, `token_v2`, `file_token`) is a small JavaScript snippet you paste into the browser console while logged in to Notion.

## Usage

1. Login to [Notion](https://www.notion.so/login) (or [app.notion.com](https://app.notion.com)) in Chrome (or any Chromium-based browser).
2. Open the developer console: `F12` → **Console** tab.
3. Paste the snippet below and press **Enter**.
4. The console prints a table of your spaces and a ready-to-use `.env` block, and copies it to your clipboard. If the auth cookies are HttpOnly (the usual case), the block contains `PASTE_TOKEN_V2_HERE` / `PASTE_FILE_TOKEN_HERE` placeholders — fill them from DevTools → **Application** → **Cookies** (see [Troubleshooting](#troubleshooting)).

## The Snippet

```javascript
(async () => {
  try {
    // Require an exact Notion registrable domain boundary so lookalikes such
    // as evilnotion.com cannot pass this credential-handling guard.
    const isNotionHost = (host) => ['notion.so', 'notion.com'].some(
      (domain) => host === domain || host.endsWith('.' + domain),
    );
    if (!isNotionHost(location.hostname)) {
      console.error('❌ This snippet must run on a Notion page (notion.so or notion.com). You are on: ' + location.hostname);
      return;
    }

    const getCookie = (name) => {
      const match = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]*)'));
      if (!match) return null;
      try {
        return decodeURIComponent(match[1]);
      } catch {
        return match[1];
      }
    };

    // Resolve the spaceId first — this works even when the auth cookies are
    // HttpOnly, because the browser sends them automatically.
    let data;
    try {
      const res = await fetch('/api/v3/getSpaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (res.status === 401) {
        console.error('❌ Notion returned 401 — your session is invalid or expired. Re-login and try again.');
        return;
      }
      if (!res.ok) {
        console.error('❌ getSpaces failed with HTTP ' + res.status);
        return;
      }
      data = await res.json();
    } catch (err) {
      console.error('❌ Request failed:', err);
      return;
    }

    // getSpaces returns a dict keyed by user ID, with the space map nested
    // under each user's "space" key (older responses had "space" at the top
    // level). Handle both shapes — mirrors test_connection() in src/core/client.py.
    const spaces = {};
    if (data && typeof data.space === 'object') Object.assign(spaces, data.space);
    for (const key of Object.keys(data || {})) {
      const payload = data[key];
      if (payload && typeof payload === 'object' && typeof payload.space === 'object') {
        Object.assign(spaces, payload.space);
      }
    }

    const entries = Object.entries(spaces);
    if (entries.length === 0) {
      console.error('❌ No spaces found for this account.');
      return;
    }

    console.log('Found ' + entries.length + ' space(s):');
    console.table(entries.map(([id, meta], i) => ({
      '#': i + 1,
      Name: (meta && (meta.name || meta.space_name)) || '(unnamed)',
      spaceId: id,
    })));

    // Always use the first space — keeps the snippet fully non-interactive
    // (prompt() dialogs are unreliable in some environments).
    const spaceId = entries[0][0];
    if (entries.length > 1) {
      console.warn('⚠️ Multiple spaces found — using the first one: ' + spaceId);
      console.warn('Need a different one? Change NOTION_SPACE_ID in your .env afterwards.');
    }

    // token_v2 and file_token are HttpOnly cookies, so document.cookie can't
    // read them. If they're missing, ask the user to paste them from
    // DevTools → Application → Cookies.
    let tokenV2 = getCookie('token_v2');
    let fileToken = getCookie('file_token');
    if (!tokenV2 || !fileToken) {
      const names = document.cookie.split(';').map((c) => c.trim().split('=')[0]).filter(Boolean);
      console.warn('⚠️ token_v2 / file_token are not readable from document.cookie (HttpOnly).');
      console.warn('Readable cookies: ' + (names.length ? names.join(', ') : '(none)'));
      console.warn('Copy the token_v2 and file_token values from DevTools → Application → Cookies → ' + location.hostname);
      console.warn('Then replace the PASTE_... placeholders in the .env block below.');
      // Preserve any independently readable cookie and add a placeholder only
      // for the HttpOnly value that the browser withheld.
      tokenV2 = tokenV2 || 'PASTE_TOKEN_V2_HERE';
      fileToken = fileToken || 'PASTE_FILE_TOKEN_HERE';
    }

    const envBlock = [
      'NOTION_SPACE_ID=' + spaceId,
      'NOTION_TOKEN_V2=' + tokenV2,
      'NOTION_FILE_TOKEN=' + fileToken,
    ].join('\n');

    console.log('\nCopy these into your .env file:\n');
    console.log(envBlock);

    try {
      await navigator.clipboard.writeText(envBlock);
      console.log('\n✅ Copied to clipboard.');
    } catch {
      // Fallback for older browsers / non-secure contexts
      const ta = document.createElement('textarea');
      ta.value = envBlock;
      document.body.appendChild(ta);
      ta.select();
      // execCommand reports whether the legacy clipboard operation succeeded;
      // do not claim success when manual copying is still required.
      const copied = document.execCommand('copy');
      document.body.removeChild(ta);
      if (copied) {
        console.log('\n✅ Copied to clipboard (fallback).');
      } else {
        console.error('\n❌ Clipboard copy failed. Copy the .env block printed above manually.');
      }
    }
  } catch (err) {
    console.error('❌ Unexpected error:', err);
  }
})();
```

## How It Works

> **Note:** the `Promise {<fulfilled>: undefined}` line the console prints after you press Enter is **normal** — it's just the browser echoing the async function's return value. The actual output (space list and `.env` block) appears as console messages **above** that line.

| Value | Source |
| --- | --- |
| `token_v2` | `token_v2` cookie — read via `document.cookie`, or filled in from DevTools when HttpOnly |
| `file_token` | `file_token` cookie — read via `document.cookie`, or filled in from DevTools when HttpOnly |
| `spaceId` | Resolved by calling `POST /api/v3/getSpaces` — the same endpoint the tool's `test_connection()` uses to verify credentials |

The `getSpaces` call runs from the Notion page itself, so your session cookies are sent automatically — no headers or tokens need to be passed manually.

## Troubleshooting

- **Only see `Promise {<fulfilled>: undefined}`** — that's the normal result of an async snippet; scroll up in the console for the output. If there are no messages at all, check the console's log level filter (the **Default levels** dropdown next to the filter box) — it may be hiding `log`/`info` messages.
- **`token_v2 / file_token are not readable from document.cookie (HttpOnly)`** — this is **normal**: Notion marks these cookies HttpOnly, so JavaScript cannot read them. The snippet prints the `.env` block with `PASTE_TOKEN_V2_HERE` / `PASTE_FILE_TOKEN_HERE` placeholders. To fill them: DevTools → **Application** → **Cookies** → `https://www.notion.so` (or `app.notion.com`) → copy the `token_v2` and `file_token` values (or: **Network** tab → click any request → **Request Headers** → `Cookie:` header).
- **`This snippet must run on a Notion page`** — you pasted it on a different site. Open [notion.so](https://www.notion.so) or [app.notion.com](https://app.notion.com), log in, and run it there.
- **`Notion returned 401`** — the session is invalid/expired. Re-login and retry.
- **Multiple spaces** — the snippet always uses the **first** space listed. If you need a different one, change `NOTION_SPACE_ID` in your `.env` afterwards. To back up more than one workspace, use a separate `.env`/deployment per space (session files are already isolated per `space_id`).
- **Clipboard copy fails** — the values are still printed in the console; select and copy them manually.

## Security

- `token_v2` and `file_token` are **full-access session credentials**. Anyone holding them can read and export your entire workspace.
- Never paste them into chat, issues, or public gists, and never commit them to git (the `.env` file is git-ignored).
- They expire when you log out or change your password — re-run the snippet to get fresh values.
