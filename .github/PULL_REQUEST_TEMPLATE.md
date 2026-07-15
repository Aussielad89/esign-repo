name: Pull request
description: Contribute a change
labels: []
body:
  - type: textarea
    id: what
    attributes:
      label: What does this change do?
    validations:
      required: true
  - type: checkboxes
    id: checks
    attributes:
      label: Checklist
      options:
        - label: Tests added/updated and `pytest tests/` passes
          required: true
        - label: README/docs updated if behavior changed
          required: false
