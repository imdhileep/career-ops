# career-ops companion (Chrome extension)

Use career-ops on any job posting without leaving the page:

- **Match score.** Skills match against your `cv.md` and title fit against your target roles. Zero tokens: this is the same regex skill check as `jd-skill-gap.mjs`, not an AI run.
- **Save to pipeline.** One click adds the job to `data/pipeline.md`, the same inbox the scanner fills. Evaluate it from the dashboard.
- **Autofill.** Fills name, email, phone, location, LinkedIn, GitHub and portfolio fields from `config/profile.yml`. Filled fields get an orange outline.

Autofill **never submits** and never clicks a button. It skips anything you already typed, file uploads and password fields. You review the form and press Submit yourself.

Works on Greenhouse, Lever, Ashby, Workday, LinkedIn, SmartRecruiters, Workable, iCIMS and BambooHR pages, and on most other job pages through their schema.org `JobPosting` data.

## Install (once)

1. Build and start the career-ops web app (see `web/README.md`).
2. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose this `extension/` folder.
3. Click the extension's **Details** → **Extension options**. The page shows your extension's ID in a command like this one:

   ```bash
   cd career-ops/web
   CAREER_OPS_ALLOWED_ORIGINS=chrome-extension://<your-extension-id> npm start
   ```

   Start the app that way from now on. Without it, the app refuses the extension. That refusal is deliberate: the app has no login, so it only answers callers you name.

4. Open a job posting and click the orange **co** icon.

The app address defaults to `http://127.0.0.1:3000`. Change it in the extension options if you run it on another port. Only local addresses are accepted.

## Privacy

The extension talks only to the career-ops app on your own computer. It reads the current page only when you open the popup. Job text is passed to the app's `/api/companion` route, written to a private temp file for the skill check and deleted right after.

## Test

```bash
cd web && npm run build && cd ..
node extension/tests/e2e.mjs
```

The test loads the extension into a real Chromium, starts the app against a throwaway data root, and checks the score, autofill (no submit, no overwrite), save, and that other websites are still refused. Set `CHROMIUM_PATH` if Playwright's own Chromium is not installed.
