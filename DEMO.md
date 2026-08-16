# Five-to-ten-minute demo

Use fictional data only. The non-persistent DEV revision automatically runs the
idempotent demo seed on startup. For a local demo, run migrations and then
`python -m scripts.seed_default_event` once.

1. Open `/` and select **St. Pölten PRIDE 2026**.
2. Click a shift card or its **Für diese Schicht anmelden** button and verify
   that the registration form opens with that shift selected. Briefly enter an
   invalid e-mail address to show immediate validation in the central error box,
   then register a fictional `example.invalid` contact for two compatible
   shifts. Show the review summary, confirmation, e-mail verification state and
   edit link. For a live mail demo, use an authorized test mailbox and
   explicitly configured SMTP credentials.
3. Use the edit link to change one shift and cancel one assignment. Explain
   that only token hashes are stored and a changed e-mail address requires a new
   confirmation.
4. Open `/admin`, review staffing metrics and enter the event planning view.
5. Add a draft shift or change capacity. Show manual waitlist promotion and
   the purpose-limited CSV and print views.
6. Open `/admin/ehrenamtliche`, filter the list, inspect a fictional person and
   demonstrate manual assignment with explicit conflict override. Show the
   controlled rejection workflow and explain that it releases active shifts.
7. In `/admin/check-in`, check a confirmed assignment in with material and then
   check it out with material return.
8. Open `/admin/outbox` and show delivery/error status. Optionally show
   `/admin/einstellungen/smtp`; the password remains an environment secret.
   All messages follow the transparent rules under
   `/admin/einstellungen/mail-templates`.
9. Finish at `/healthz`, `/health/live` and `/health/ready`; verify the deployed
   version equals the expected `dev` commit SHA.

## Demo URLs

- Public landing page: `/`
- Registration: `/veranstaltungen/pride-2026/anmeldung`
- Admin dashboard: `/admin`
- Volunteers: `/admin/ehrenamtliche`
- Check-in: `/admin/check-in`
- Outbox preview: `/admin/outbox`
- Mail flow and templates: `/admin/einstellungen/mail-templates`
- Diagnostics: `/healthz`, `/health/live`, `/health/ready`, `/admin/db`
