# Crypto Spread Monitor — Sequential Development TODO

Основан на архитектурном документе `base1.pdf` (v1.1).

Порядок задач построен так, что каждая следующая опирается на предыдущую.
Фазы 1, 2, 3 можно вести параллельно после завершения Фазы 0.

---

## Phase 0 — Инфраструктура проекта

- [x] **T01** Создать структуру директорий согласно разделу 7 архитектуры:
  `symbol_discovery/`, `collectors/`, `normalizer/`, `shm/`, `spread_reader/`,
  `infra/`, `config/`, `cache/`, `snapshots/`, `logs/`

- [x] **T02** Составить `requirements.txt` / `pyproject.toml`
  Зависимости: `aiohttp`, `websockets`, `pyyaml`, `structlog`,
  `posix_ipc` (или встроенный `mmap`), `prometheus_client` (опционально)

- [x] **T03** Создать `config/config.yaml`
  Параметры: `MAX_SLOTS`, `SLOT_SIZE`, `STALENESS_THRESHOLD_MS`,
  интервал snapshot, пути к файлам, `quote_currency: USDT`

- [x] **T04** Разработать `infra/logging_config.py`
  Структурированное логирование: формат JSON, ротация файлов, уровни по компонентам

- [x] **T05** Создать `Makefile` с командами:
  `init-shm`, `run`, `stop`, `clean-shm`, `snapshot`

---

## Phase 1 — Symbol Discovery

- [ ] **T06** Исследовать REST API endpoints Binance (Spot + USDT-M Futures) и Bybit
  (Spot + Linear Futures) — задокументировать URL и поля активности пары
  (`status`, `isTrading` и т.п.)

- [ ] **T07** Исследовать и задокументировать форматы символов для каждой биржи/рынка
  — разделители, суффиксы (PERP, _SWAP, и т.п.)

- [ ] **T08** Разработать `symbol_discovery/symbol_normalizer.py`
  Функции: `raw_to_unified(exchange, market, raw) → "BASE-QUOTE"`,
  `unified_to_raw(exchange, market, unified) → str` (обратный маппинг)

- [ ] **T09** Разработать `symbol_discovery/base_exchange_info.py`
  Абстрактный класс с методом `fetch_symbols() → list[str]`

- [ ] **T10** Разработать `symbol_discovery/binance_exchange_info.py`
  Классы: `BinanceSpotInfo`, `BinanceFuturesInfo` — HTTP GET + фильтр активных пар

- [ ] **T11** Разработать `symbol_discovery/bybit_exchange_info.py`
  Классы: `BybitSpotInfo`, `BybitFuturesInfo`

- [ ] **T12** Разработать `symbol_discovery/intersection.py`
  Логика пересечения: символ включается, если он есть в spot И futures
  хотя бы одной биржи → возвращает `subscription_lists` (dict, 4 ключа)

- [ ] **T13** Разработать `symbol_discovery/discovery_runner.py`
  Оркестратор: 4 источника → нормализация → пересечение → сохранение
  `cache/subscription_lists.yaml`, периодическое обновление

- [ ] **T14** Принять решения по открытым вопросам (раздел 3.5 PDF):
  - Фильтр по volume (без фильтра / из конфига)?
  - Поведение при недоступности REST при старте (аварийная остановка / fallback на кэш)?
  - Частота обновления списков (раз в час / раз в сутки / по сигналу)?
  - Поведение при исчезновении символа с биржи (удалить из SHM / помечать stale)?
  - Фильтрация по quote currency (только USDT / из конфига)?

---

## Phase 2 — Normalizer (контракт данных)

- [ ] **T15** Разработать `normalizer/schema.py`
  Датакласс `Quote`: `exchange`, `market_type`, `unified_symbol`,
  `bid: float`, `ask: float`, `ts_exchange_ns: int`, `ts_recv_ns: int`

- [ ] **T16** Исследовать wire-форматы WebSocket сообщений Binance и Bybit
  (spot + futures) — задокументировать JSON-структуру best bid/ask стрима

- [ ] **T17** Разработать `normalizer/normalizer.py` — 4 парсера:
  `parse_binance_spot`, `parse_binance_futures`,
  `parse_bybit_spot`, `parse_bybit_futures`
  + валидация Quote: `bid > 0`, `ask > 0`, `bid <= ask`, все поля заполнены

---

## Phase 3 — SHM-таблица

- [ ] **T18** Разработать `shm/shm_layout.py`
  Константы: `MAGIC`, `VERSION`, `HEADER_SIZE`, `SLOT_SIZE`, `MAX_SLOTS`
  Offsets каждого поля слота.
  Разметка слота:
  `seq_begin (8B)` | `bid (8B)` | `ask (8B)` | `ts_ns (8B)` |
  `symbol (32B)` | `exchange (8B)` | `market (4B)` | `seq_end (8B)`

- [ ] **T19** Разработать `shm/shm_init.py`
  Инициализация POSIX SHM `/csm_quotes_v1`:
  open → проверить MAGIC → пересоздать если не совпадает →
  записать заголовок → обнулить слоты → mmap с MAP_SHARED

- [ ] **T20** Разработать `shm/shm_writer.py`
  Seqlock write protocol:
  `seq_begin += 1` → memory barrier → записать поля → memory barrier → `seq_end = seq_begin`
  Маппинг `(exchange, market_type, unified_symbol) → slot_id`,
  выделение новых слотов при появлении нового символа

- [ ] **T21** Разработать `shm/shm_reader.py`
  Seqlock read protocol: spin до чётного `seq_begin`, читать поля, проверить `seq_end == seq`
  Метод чтения всех активных слотов

- [ ] **T22** Разработать `shm/shm_cleaner.py`
  Безопасное удаление SHM-сегмента при остановке/рестарте
  Поведение при исчерпании MAX_SLOTS: предупреждение в лог, без остановки системы

---

## Phase 4 — Collectors

- [ ] **T23** Исследовать WebSocket каналы Binance и Bybit для best bid/ask:
  - Binance: `bookTicker` stream (spot + futures)
  - Bybit: `orderbook.1` (spot + futures)
  - Механизмы keepalive / ping-pong / heartbeat (различаются между биржами)
  - Модель подключения: мульти-подписка vs один поток на символ

- [ ] **T24** Разработать `collectors/base_collector.py`
  Абстрактный класс: `connect()`, `subscribe(symbols)`,
  `handle_message(raw)`, `reconnect()`
  Стратегия reconnect: exponential backoff, логирование каждой попытки

- [ ] **T25** Разработать `collectors/binance_spot_collector.py`
  Подключение к Binance Spot WS, подписка на bookTicker, keepalive

- [ ] **T26** Разработать `collectors/binance_futures_collector.py`
  Подключение к Binance USDT-M Futures WS

- [ ] **T27** Разработать `collectors/bybit_spot_collector.py`
  Подключение к Bybit Spot WS, ping/pong протокол

- [ ] **T28** Разработать `collectors/bybit_futures_collector.py`
  Подключение к Bybit Linear Futures WS

- [ ] **T29** Определить транспорт collector → normalizer
  Рекомендация: `asyncio.Queue`. Исследовать альтернативы (pipe, shared buffer),
  задокументировать обоснование выбора

- [ ] **T30** Разработать `collectors/collector_runner.py`
  Запустить все 4 коллектора согласно `subscription_lists`,
  прокидывать сырые сообщения в normalizer через Queue

---

## Phase 5 — Интеграция Normalizer → SHM

- [ ] **T31** Доработать `normalizer/normalizer.py` — полный pipeline:
  `Queue.get()` → парсер (по типу источника) → валидация Quote →
  `shm_writer.write(quote)`

---

## Phase 6 — Spread Reader

- [ ] **T32** Разработать `spread_reader/spread_calculator.py`
  Формула: `spread = (best_ask_futures − best_bid_spot) / best_bid_spot × 100%`
  Проверки перед расчётом:
  - Оба значения свежие: `age < STALENESS_THRESHOLD_MS`
  - `best_bid_spot > 0`
  - Отрицательный спред допустим (бэквордация) — не обрезать

- [ ] **T33** Разработать `spread_reader/snapshot_writer.py`
  Формат файла (раздел 5.2 PDF):
  `spread_snapshot_<ISO8601>.txt`, заголовок с метаданными, колонки:
  `SYMBOL | SPREAD% | ASK_FUT | BID_SPOT | FUT_EXCH | SPOT_EXCH | STALE`
  Атомарная запись: temp-файл → `os.rename()`
  Для stale: писать `N/A`. Политика ротации из конфига

- [ ] **T34** Разработать `spread_reader/spread_runner.py`
  Runner с интервалом из конфига, graceful shutdown по SIGINT/SIGTERM

---

## Phase 7 — Наблюдаемость

- [ ] **T35** Разработать `infra/metrics.py`
  Метрики:
  - `quotes_received_total{exchange, market}` — counter
  - `shm_write_latency_ns` — histogram
  - `spread_snapshots_total` — counter
  - `stale_quotes_total{symbol}` — counter
  - `collector_reconnects_total{exchange, market}` — counter
  - `shm_slots_used` — gauge

- [ ] **T36** Разработать `infra/health_check.py`
  Проверка свежести данных в SHM и доступности всех коллекторов

---

## Phase 8 — Финальная интеграция и тестирование

- [ ] **T37** Написать интеграционный тест полного pipeline:
  mock WS → collector → normalizer → SHM → spread_reader → snapshot файл

- [ ] **T38** Проверить end-to-end запуск через `make run`

- [ ] **T39** Проверить `make clean-shm` и корректный рестарт:
  повторная инициализация SHM, MAGIC check, пересоздание при несовпадении

- [ ] **T40** Проверить graceful shutdown всех компонентов (SIGINT/SIGTERM)

---

## Зависимости между фазами

```
Phase 0 (инфра)
    ├── Phase 1 (symbol discovery)  ─┐
    ├── Phase 2 (schema/normalizer)  ├── могут идти параллельно
    └── Phase 3 (SHM)               ─┘
                                      ↓
                                 Phase 4 (collectors)
                                      ↓
                                 Phase 5 (normalizer→SHM pipeline)
                                      ↓
                                 Phase 6 (spread reader)
                                      ↓
                                 Phase 7 (observability)
                                      ↓
                                 Phase 8 (integration tests)
```

## Критические файлы (основа архитектуры)

| Файл | Роль |
|------|------|
| `config/config.yaml` | Все параметры системы |
| `normalizer/schema.py` | Контракт `Quote` — основа межкомпонентного взаимодействия |
| `shm/shm_layout.py` | Разметка POSIX SHM — фундамент pipeline |
| `symbol_discovery/intersection.py` | Логика отбора торгуемых пар |
| `collectors/base_collector.py` | Базовый класс с reconnect-стратегией |
| `spread_reader/spread_calculator.py` | Формула спреда |
