# Configuration Reference

All system parameters live in `config/config.yaml`. The file is loaded once at startup using `pyyaml`:

```python
import yaml
with open("config/config.yaml") as f:
    config = yaml.safe_load(f)
```

---

## `shm` — Shared Memory

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `name` | string | `/csm_quotes_v1` | POSIX SHM segment name. Must start with `/`. Changing this requires `make clean-shm` + `make init-shm`. |
| `max_slots` | int | `2048` | Maximum number of concurrent quotes (one slot per exchange+market+symbol triple). Exceeding this logs a warning but does not crash. |
| `slot_size` | int | `76` | Slot size in bytes. Must match the seqlock layout in `shm/shm_layout.py`. Do not change without updating the layout constants. |

**Total SHM size** = header + `max_slots × slot_size` ≈ 152 KB at defaults.

---

## `timing` — Intervals and Thresholds

| Key | Type | Default | Unit | Description |
|-----|------|---------|------|-------------|
| `staleness_threshold_ms` | int | `2000` | ms | A quote older than this is considered stale. Stale quotes appear as `N/A` in snapshots and increment the `stale_quotes_total` metric. |
| `snapshot_interval_s` | int | `5` | seconds | How frequently the spread reader writes a snapshot file. Lower values increase I/O. |
| `discovery_refresh_interval_s` | int | `3600` | seconds | How often symbol discovery re-fetches from exchange REST APIs. Set to `0` to disable periodic refresh (fetch once at startup only). |

---

## `paths` — File Locations

All paths are relative to the project root unless they start with `/`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `cache_dir` | string | `cache` | Directory for `subscription_lists.yaml`. Created on first run. |
| `snapshot_dir` | string | `snapshots` | Directory for spread snapshot `.txt` files. |
| `log_dir` | string | `logs` | Directory for rotating log files (`csm.log`). |

---

## `quote_currency`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `quote_currency` | string | `USDT` | Only pairs quoted in this currency are subscribed to. Case-sensitive. |

---

## `logging` — Log Levels

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `level` | string | `INFO` | Default log level for all components. Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `levels.<component>` | string | (inherited) | Per-component override. Component names match top-level Python package names. |

**Available component names:**

| Name | Module |
|------|--------|
| `symbol_discovery` | `symbol_discovery/` |
| `collectors` | `collectors/` |
| `normalizer` | `normalizer/` |
| `shm` | `shm/` |
| `spread_reader` | `spread_reader/` |
| `infra` | `infra/` |

**Example — enable DEBUG only for collectors:**

```yaml
logging:
  level: INFO
  levels:
    collectors: DEBUG
```

---

## Full Default Configuration

```yaml
shm:
  name: /csm_quotes_v1
  max_slots: 2048
  slot_size: 76

timing:
  staleness_threshold_ms: 2000
  snapshot_interval_s: 5
  discovery_refresh_interval_s: 3600

paths:
  cache_dir: cache
  snapshot_dir: snapshots
  log_dir: logs

quote_currency: USDT

logging:
  level: INFO
  levels:
    symbol_discovery: INFO
    collectors: DEBUG
    normalizer: INFO
    shm: WARNING
    spread_reader: INFO
    infra: INFO
```
