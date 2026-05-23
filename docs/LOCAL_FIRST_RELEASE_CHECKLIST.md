# AskChappy Local-First Release Checklist

Use this checklist before local production/local MVP handoff.

## Verification commands
- [ ] `npm install`
- [ ] `npm test`
- [ ] `npm run lint`
- [ ] `npm run verify`
- [ ] `npm run start` launches local-first runtime at `http://127.0.0.1:4173/chappy`
- [ ] `npm run smoke:local-runtime` passes as noninteractive local runtime wiring check

## Contract and route checks
- [ ] Confirm canonical routes are active and unchanged (`/chappy`, `/chappy/session/:sessionId`, `/chappy/summary/:sessionId`, admin routes).
- [ ] Confirm retired `/demo*` and `/visual-session*` routes remain inactive.
- [ ] Confirm transcript model uses `text` and does not use `content`.
- [ ] Confirm mode changes are kept in metadata/session events (not fake transcript messages).
- [ ] Confirm summary/recap remains grounded in canonical transcript + metadata.

## Auth/admin checks
- [ ] Confirm email-only local auth behavior is unchanged.
- [ ] Confirm `jsiejk@ddn.com` resolves to `admin` and other emails to `standard_user`.
- [ ] Confirm Voice Studio controls are admin-only and not shown in normal user sessions.
- [ ] Confirm avatar admin controls are admin-only and not shown in normal user sessions.

## Local-first production and asset safety checks
- [ ] Confirm local-first/local production/local MVP terminology is used.
- [ ] Confirm no private voice/avatar assets were committed.
- [ ] Confirm no model/cloud/db integrations were accidentally introduced.
- [ ] Confirm standard voice remains active/default for synthesis.
- [ ] Confirm cloned voice readiness gate does not claim active cloned synthesis without approved provider adapter + prerequisites.
- [ ] Confirm no cloud voice provider runtime was added.
