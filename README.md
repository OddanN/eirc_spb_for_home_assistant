<!-- Version: 0.0.1 -->

# EIRC SPB for Home Assistant

Custom integration for Home Assistant to fetch data from EIRC Saint Petersburg.

## Status

Project scaffold is prepared. The repository currently contains the base structure for a HACS-compatible custom
integration, but the API client and entities are not implemented yet.

## Planned functionality

- authorization in the EIRC account
- retrieval of account and billing data
- Home Assistant entities for balances, charges, and related metrics

## Repository structure

```text
custom_components/eirc_spb_for_home_assistant/
  __init__.py
  const.py
  manifest.json
```

## Installation

### Via HACS

1. Add this repository as a custom repository in HACS with type `Integration`.
2. Install `EIRC SPB`.
3. Restart Home Assistant.

### Manual

1. Copy the `custom_components/eirc_spb_for_home_assistant` directory into your Home Assistant configuration directory.
2. Restart Home Assistant.

## Development

The integration domain is `eirc_spb_for_home_assistant`.
