# Developer guide

This project is maintained through AI coding agents: the owner doesn't write code. These pages are written so an agent (or a person) can find the right place, make a change safely, and prove it works, without reading the whole codebase.

**Start here:** [AGENTS.md](../../AGENTS.md) has the rules every change must follow.

| Read | When |
|---|---|
| [Architecture](architecture.md) | To find your way around: the three layers, every file's job, the contracts between them, how a panel event becomes a Home Assistant state |
| [Making changes](making-changes.md) | Before changing anything: where each kind of change goes, recipes (fix a bug, add a sensor, add an option, decode a new message…), the checklist before pushing, releasing |
| [Testing](testing.md) | To run the checks, use the simulated panels, and write tests |
| [What we know about the panel](protocol.md) | When the change involves how the panel behaves. Add what you learn |
| [Live test plan](../testing/live-test-plan.md) | Before testing on a real panel |

## In one minute

- **Three layers**: protocol (bytes) → panel drivers (talking to the panel) → Home Assistant glue (screens, entities, notices). Only the last imports Home Assistant.
- **Compartments**: each module has one job; each has its own test file. Change one compartment at a time.
- **Contracts**: the `TexecomPanel` interface, the `texecom_event` events, entity IDs, the stored settings, and the blueprints' inputs. People's setups depend on them; don't change them without a plan.
- **Safety**: never arm or disarm a real panel unless its owner is there and has agreed.
- **Checks**: `ruff check .`, `ruff format --check .`, the driver tests (Python 3.13) and the Home Assistant tests (Python 3.14). CI runs them on every push.

---

[Documentation index](../README.md)
