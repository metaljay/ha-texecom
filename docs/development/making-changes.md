# Making changes

How to change something safely: find the compartment, prove the problem with a test, change only that compartment, run the checks.

**On this page:** [The workflow](#the-workflow) · [Where does my change go?](#where-does-my-change-go) · [Recipes](#recipes) · [Before you push](#before-you-push) · [Commits and pull requests](#commits-and-pull-requests) · [Releasing](#releasing)

## The workflow

1. **Understand the request in plain words.** What should a user see or be able to do afterwards? If that's unclear, ask the owner before writing code.
2. **Find the compartment** in the table below. Read that module and its test file; you shouldn't need the rest.
3. **Write a test first** in that compartment's test file that shows the problem or the new behaviour. Run it and see it fail.
4. **Change the code**, in that compartment only. If you find yourself editing a second compartment, check whether you're crossing a [contract](architecture.md#the-contracts).
5. **Run that test file**, then **everything** ([Testing](testing.md)): ruff, both test suites.
6. **Update the docs** users will read (`docs/user/`), and [protocol.md](protocol.md) if you learned something about the panel.
7. **Commit** with a message that says what changed for users and why ([below](#commits-and-pull-requests)).

Keep each change small: one bug or one feature per commit. A refactor (moving code) never goes in the same commit as a behaviour change.

## Where does my change go?

All code paths are under `custom_components/texecom/`; test paths are under `tests/`.

| You want to… | Change | Test in |
|---|---|---|
| Decode a new panel message, log type or command (Connect) | `connect/protocol.py` | `test_connect.py` |
| …or a new Crestron line or frame | `crestron/protocol.py` | `test_crestron.py` |
| React to a panel event: alarms, who armed, failed arms | `connect/events.py` | `test_connect.py`, then `ha/test_alarm_control_panel.py` |
| Tamper, faults, mains power | `connect/conditions.py` | `test_connect.py`, `ha/test_binary_sensor.py` |
| The panel clock | `connect/clock.py` | `test_connect.py`, `ha/test_issues.py`, `ha/test_repairs.py` |
| Reading zones and areas from the panel | `connect/discovery.py` | `test_connect.py`, `ha/test_flows_options.py` |
| Connecting, reconnecting, timings, arm/disarm (Connect) | `connect/panel.py`, `connect/client.py` | `test_connect.py` |
| The Crestron connection or commands | `crestron/connection.py` | `test_crestron.py` |
| Crestron events, arming | `crestron/panel.py` | `test_crestron.py`, `ha/test_init.py` |
| Area states, names, the offline grace (shared by both drivers) | `panel.py` (a contract: careful) | both driver test files |
| How the alarm entity behaves, or the message when arming fails | `alarm_control_panel.py` | `ha/test_alarm_control_panel.py` |
| Names for keypad users | `users.py` (the option's text), used by `alarm_control_panel.py` and `logbook.py` | `ha/test_users.py`, `ha/test_flows_options.py` |
| What the activity list (Logbook) says about an event | `logbook.py` | `ha/test_logbook.py` |
| Zone, tamper, problem, mains or connection sensors | `binary_sensor.py` | `ha/test_binary_sensor.py` |
| Voltages, currents, keypad display | `sensor.py` | `ha/test_init.py` (add `ha/test_sensor.py` if you change it) |
| What every entity shares: availability, device names, entity IDs, rooms | `entity.py` | `ha/test_entity.py`, `ha/test_init.py` |
| The setup screens | `flows/connect.py`, `flows/crestron.py`, `config_flow.py` (menu, arm modes), `flows/validation.py` | `ha/test_flows_connect.py`, `ha/test_flows_crestron.py`, `ha/test_config_flow.py` |
| The options menu (**Configure**) | `flows/options.py` | `ha/test_flows_options.py` |
| Reauth or reconfigure | `flows/reauth_reconfigure.py` | `ha/test_flows_reauth_reconfigure.py` |
| Building the driver from an entry; `texecom_event`; reload on layout change | `factory.py` | `ha/test_init.py` |
| Setup/unload; which option changes reconnect (`DRIVER_OPTIONS` in `const.py`) | `__init__.py` | `ha/test_init.py`, `ha/test_flows_options.py` |
| A notification (*Alarm not set*, *on battery*, *tamper*) | `notifications.py` | `ha/test_notifications.py` |
| A Repairs notice, or its Fix | `issues.py`, `repairs.py` | `ha/test_issues.py`, `ha/test_repairs.py` |
| The Alarm dashboard | `dashboard.py` | `ha/test_dashboard.py` |
| Diagnostics | `diagnostics.py` | `ha/test_diagnostics.py` |
| Any words on screen | `strings.json`, then copy it to `translations/en.json` | (hassfest checks the format) |
| Entity icons | `icons.json` | (hassfest checks the format) |
| The ready-made automations | `blueprints/automation/texecom/` (outside `custom_components/`) | `ha/test_blueprints.py` |
| What users read | `docs/user/`, `README.md` | (none) |

## Recipes

### Fix a bug someone reported

1. Get the facts: what they did, what they expected, what happened, a rough time, their panel and firmware, and the diagnostics file. Ask for a debug log if needed (see [Troubleshooting](../user/troubleshooting.md#getting-more-detail)).
2. Find the compartment from the symptoms (table above). Wrong state → the driver (`connect/events.py`, `connect/panel.py`); wrong wording → `strings.json`; wrong entity → the platform file.
3. **Reproduce it in a test.** The simulated panel can do most things a real one does: `FakeConnectPanel` has `set_zone`, `set_area`, `send_user`, `send_log`, `drop_all` (hang up, as during an alarm report), `nak_next` (refuse a command), `on_battery`, `power_override`, `clock_offset`, `clock_raw`. See [Testing](testing.md#the-simulated-panels).
4. Fix it in that compartment, run the checks, and say in the commit what the user saw before and sees now.
5. If it came from a real panel's behaviour, write the behaviour into [protocol.md](protocol.md).

### The panel sends something the integration doesn't understand

You'll see it in a debug log: `Connect: message {...}` with `kind: unknown`, an area state above 5, or a log type nothing handles.

1. **Decode it** in `connect/protocol.py` (`decode_message`, or a new name in `TAMPER_LOG_NAMES` / `FAULT_LOG_NAMES`), with a decoding test in `tests/test_connect.py`.
2. **Act on it** in the driver compartment it belongs to: alarms and users → `connect/events.py`; tampers, faults, power → `connect/conditions.py`; area state → `_apply_area` in `connect/panel.py`. Add a driver test that sends it from `FakeConnectPanel` (e.g. `fake.send_log(type, group, parameter)`).
3. **Show it in Home Assistant** only through the `TexecomPanel` interface: update `zones`/`areas`/`extra` and call `notify()`, or fire `on_event`. Then the entity or event code in layer 3 picks it up.
4. **Write it down** in [protocol.md](protocol.md): what it is, when it was seen, which panel and firmware.

### Add a sensor

1. Decide where its value comes from. If the driver doesn't have it yet, add it to `panel.extra` in the right driver compartment first (with a driver test).
2. Add the entity class to the platform file (`binary_sensor.py` or `sensor.py`), with a `translation_key`, and create it in that file's `async_setup_entry`. Give it a unique ID key that will never change, and an `object_id` so its entity ID is `*.texecom_<name>`.
3. Add its name under `entity` in `strings.json` (and copy to `translations/en.json`).
4. Test it in `tests/ha/test_<platform>.py`. If it should appear on the Alarm dashboard, update `dashboard.py` and `ha/test_dashboard.py`.
5. Add it to [Using it](../user/using.md#what-youll-see).

### Add an option

The options are a menu (**Configure**), with one small screen per entry.

1. Add the key (and default) to `const.py`. If the driver is built with it (`factory.py` reads it), also add it to `DRIVER_OPTIONS`, so changing it reconnects. Otherwise it applies straight away, so the code that uses it must read `entry.options` each time, not once at setup.
2. Add it to a screen in `flows/options.py`: a field on an existing screen, or a new screen (a menu entry in `async_step_init`'s list, and an `async_step_<entry>` that shows a form and saves with `self._save({...})`). Its words go in `strings.json` under `options.step.<entry>`, and the menu label under `options.step.init.menu_options`.
3. Use it where it matters: in `factory.py` if the driver needs it, or in the entity or notification that uses it.
4. Test the screen in `ha/test_flows_options.py` (`options(hass, entry, "<entry>", key=value)` picks the menu entry and submits it) and the effect in the compartment's test file. Check whether it reconnects: count `fake.connections` before and after.
5. Add it to the options table in [Using it](../user/using.md#options), marked *reconnects to the panel* if it does.

Options are stored in the config entry, so a new option needs a sensible default for entries saved before it existed: read it with `.get(KEY, DEFAULT)`.

### Change words on screen

1. Edit `strings.json`. Keep the tone of the rest: plain words, short sentences, say what to do next.
2. Copy it over `translations/en.json` (they must be identical).
3. Links can't be written into `strings.json` (hassfest rejects URLs there). Add the link to `const.py` (`HELP_*`) and pass it in as `{help}` with `description_placeholders`; the text then says `[the setup guide]({help})`.
4. Run the checks (hassfest validates the file).

### Add a notification or a Repairs notice

- **A notification about something that happened** (like *Alarm not set*) goes in `notifications.py`: one function that creates it from the event and one that removes it when it no longer applies, wired up from `factory.py` (events) or `__init__.py` (listeners).
- **A notification that lasts while something lasts** (like *Alarm panel on battery* or *Alarm tamper*) goes in `watch_conditions` in `notifications.py`. It runs on every change the panel reports, and only touches a notification when its text changes. Add a setting to turn it off on the **Notifications** screen of the options.
- **A Repairs notice** goes in `issues.py`, with its text under `issues` in `strings.json`. If it can offer a **Fix**, that goes in `repairs.py`.
- Test in `ha/test_notifications.py` / `ha/test_issues.py` / `ha/test_repairs.py`.

### Describe a new event in the activity list

`logbook.py` turns each `texecom_event` into a sentence against the area's alarm (*House Alarm reported a fault: AC Fail*). For a new event type, add a branch to `_message`, and a test in `ha/test_logbook.py` that fires the event and checks the sentence.

### Make a screen wait for something slow

Setup, reauth, reconfigure and *read zones again* talk to the panel, which can take up to a minute. They show a progress screen meanwhile instead of a frozen form:

1. Start the slow work as a task (`self.hass.async_create_task(...)`), keep it on the flow, and return `self.async_show_progress(step_id=..., progress_action=..., progress_task=task)`. The progress text goes in `strings.json` under `progress.<progress_action>`.
2. Home Assistant runs the same step again when the task finishes: read the result there, and return `self.async_show_progress_done(next_step_id=...)` to move on (to the next screen, or back to the form with the error).
3. In tests, `after_progress(hass, result)` (in `tests/ha/common.py`) waits for the task and moves the flow on.

See `flows/connect.py` (`async_step_connect_check`) for the pattern.

### Add or change a ready-made automation (blueprint)

1. Edit the YAML in `blueprints/automation/texecom/`. Input names are a [contract](architecture.md#the-contracts): people's automations store them. Add new inputs with a default.
2. Test it in `tests/ha/test_blueprints.py`: `install()` puts the blueprint in a test config folder, `use()` makes an automation from it, then drive the simulated panel and check what happened. A phone notification can't run in tests, so `alerts_for_test()` swaps it for an event the test can see.
3. The import buttons in [Automations](../user/automations.md) point at the file on `main`, so a new blueprint reaches users once it's merged. Don't rename or move one.

### Change what's stored in the config entry

This is a [contract](architecture.md#the-contracts): people's saved setups must keep working.

1. Bump `MINOR_VERSION` (compatible additions) or `VERSION` (incompatible changes) on `TexecomConfigFlow` in `config_flow.py`.
2. Add `async_migrate_entry` to `__init__.py`, turning old entries into the new shape.
3. Add a test that creates a `MockConfigEntry` the old way and checks it loads, with the same entity IDs.

### Keep up with a new Home Assistant release

1. Update `pytest-homeassistant-custom-component` in `.github/workflows/validate.yml` (and the README's development notes) to the version for the new release.
2. Run the Home Assistant tests; fix any failures and any new deprecation warnings.
3. If you start using something only newer releases have, raise `homeassistant` in `hacs.json`.

## Before you push

- [ ] `ruff check .` and `ruff format --check .` are clean.
- [ ] Driver tests and Home Assistant tests pass ([Testing](testing.md)).
- [ ] New behaviour has a test; the docs say what users will see.
- [ ] `strings.json` and `translations/en.json` are identical.
- [ ] No [contract](architecture.md#the-contracts) changed, or the pull request says how and why.
- [ ] Layers 1–2 still don't import Home Assistant (`grep -r homeassistant custom_components/texecom/connect custom_components/texecom/crestron custom_components/texecom/panel.py` finds nothing).
- [ ] Nothing private in the diff: real names, emails, codes, home details. Commits are by `metaljay <metaljay@users.noreply.github.com>`.
- [ ] Re-read your own diff as a reviewer would.

CI (`.github/workflows/validate.yml`) runs the same checks on every push, plus HACS and hassfest validation, and again weekly.

## Commits and pull requests

- **One change per commit**, with a first line that says what changed for users, e.g. *"Mains power: don't clear a remote PSU's AC fail from the panel's own readings"*.
- **The body** says what the user saw before, what they see now, and anything they should check.
- Pull requests repeat that for the whole change, list what was tested (and on what: tests, the owner's test Home Assistant, a real panel), and what the owner should check before merging.

## Releasing

Only when the owner asks. Patch increments (0.2.3, 0.2.4…).

1. Everything is merged to `main`, CI is green, and the change was checked on the owner's test Home Assistant (and the real panel if it touched the driver).
2. Bump `version` in `custom_components/texecom/manifest.json`, commit (*"0.2.3"*), push.
3. Create a GitHub release tagged `v0.2.3` with plain-English notes: what's new, what's fixed, anything users need to do. (The release workflow checks the tag matches `manifest.json`.)
4. HACS offers the update to everyone; update the owner's Home Assistant through HACS and restart.

---

[← Architecture](architecture.md) · [Testing →](testing.md)
