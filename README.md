# Škola OnLine — Marks (Home Assistant)

[Čeština](README.cs.md)

Reads children's marks (grades) from the Czech school information system **Škola OnLine**
into Home Assistant. One config entry per parent account, one sensor entity per child.

> **Status: early / unverified against a real account.** The integration is built from
> Škola OnLine's published [OpenAPI spec](https://libre-skolaonline.github.io/API-docs/), which
> does not document how a parent account's list of children is returned — please open an issue
> if something doesn't match your account.

## What it does

- Logs in to Škola OnLine (OAuth2 password grant) and finds the marks for each configured child.
- One sensor per child, **stable across semesters and school years** — no entity churn at the
  start of a new term.
- `state` = overall average for the current semester (average of each subject's own weighted
  average, same convention as a report card).
- Attributes: current semester/school year, per-subject breakdown with each subject's average
  and its most recent marks (a handful per subject, not the full history — see below).
- Subjects show up even before they have a single mark yet — the full subject list comes from
  the timetable, not just from subjects that already have a grade.
- A `skolaonline_znamky_new_mark` event fires when a genuinely new mark appears (tracked across
  restarts, so you won't get old marks re-announced as "new").
- A `skolaonline_znamky.get_marks` service fetches the **complete** mark list (including the
  theme/teacher comment fields not kept in attributes) for a child/semester/subject on demand —
  intended for a future "show all marks" Lovelace card, not included in this integration.

## Why not put the whole history in the sensor attributes?

Home Assistant's recorder only stores a limited amount of attribute data per state (~16 kB) and
warns/drops attributes above that. Keeping only a short recent list per subject in the sensor,
and fetching the rest via the `get_marks` service when actually needed, keeps every entity well
under that limit no matter how many marks accumulate over a school year.

## Installation (HACS)

Add this repository as a custom HACS repository (category: Integration), install, restart Home
Assistant, then add the integration from Settings → Devices & services.

## Configuration

1. Enter your Škola OnLine parent account username and password.
2. Select which of the automatically detected children to track. If a child isn't found
   automatically (see the status note above), you can add it manually by student ID.

Options (Settings → Devices & services → this integration → Configure) let you change the
tracked children later, the polling interval, and how many recent marks per subject are kept in
attributes.

## Known limitations

- Only marks for the *current* semester are exposed as live entity state. Historical semesters
  are reachable via the `get_marks` service, not as separate entities.
- No Lovelace card is included yet.

## Credits

Inspired by [`schizza/bakalari-ha`](https://github.com/schizza/bakalari-ha) and
[`VitisEK/home-assistant-bakalari`](https://github.com/VitisEK/home-assistant-bakalari) (same
problem for the Bakaláři school system) and
[`elvisek2020/hacs-calendar_skolaonline`](https://github.com/elvisek2020/hacs-calendar_skolaonline)
(timetable integration for Škola OnLine, auth-flow reference).
