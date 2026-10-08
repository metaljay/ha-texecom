# Documentation

## For people using it

| Page | What's in it |
|---|---|
| [Setting it up](user/setup.md) | Installing from HACS, adding the integration, part arms, coming from Homebridge |
| [Using it](user/using.md) | The alarm's states, the sensors, the Alarm dashboard, options, notifications, the activity list, codes, good to know |
| [Apple Home](user/apple-home.md) | The alarm in the Home app through HomeKit Bridge |
| [Automations](user/automations.md) | One-click automations (arm when everyone leaves, alerts on your phone), the `texecom_event` events |
| [Troubleshooting](user/troubleshooting.md) | What error messages mean, Repairs notices, logs, diagnostics, known limits |
| [Crestron](user/crestron.md) | Using a COM port set to Crestron instead of a SmartCom |

## For people (and AI agents) working on the code

Start with [AGENTS.md](../AGENTS.md): the rules, and how to make a change safely.

| Page | What's in it |
|---|---|
| [Developer guide](development/README.md) | Where to start |
| [Architecture](development/architecture.md) | The layers and compartments: which file does what, and the contracts between them |
| [Making changes](development/making-changes.md) | Where each kind of change goes, step-by-step recipes, fixing a bug, releasing |
| [Testing](development/testing.md) | The test suites, the simulated panels, running everything locally |
| [What we know about the panel](development/protocol.md) | Texecom Connect and Crestron as seen on a real panel, and what's still unknown |
| [Other projects](development/other-projects.md) | Other Texecom projects and alarm integrations: what they offer, what we took, ideas not done yet |
| [Live test plan](testing/live-test-plan.md) | Testing on a real panel: safety rules, test cases, protocol mapping, fringe cases |
