# Speiro FHIR publication destination

The authoritative source is `Speiro-Inc/ABA_Wearable` under `fhir/verification/`.
This repository contains deployment plumbing and retained reviewed releases;
it is not an independently maintained FHIR source copy.

Publication history already retains release 0.1.0. Never run empty-history
bootstrap over a released site. Publishing requires separate owner authorization,
protected-environment approval, reviewed source/history commits and the documented
hosting-main write freeze through deployment verification.

See [Publication history safeguard](HISTORY_SAFEGUARD.md) for local checks, the PR
validation workflow, its trust boundary, and instructions to require its status
in main ruleset 24856755 after it has run successfully. Merely adding the workflow
does not make the check required or complete publication acceptance.
