# Remove Candle Volume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** เปลี่ยน completed market-data fact และ deterministic replay จาก OHLCV เป็น OHLC พร้อมลบ Volume panel ออกจาก Candlestick chart โดยไม่เปลี่ยนผลการเทรด

**Architecture:** `Candle` เป็น immutable OHLC fact เพียงรูปแบบเดียวของ application; Binance adapter ทิ้ง transport volume และ replay reader รับ CSV ห้าคอลัมน์เท่านั้น `CandlestickChartWidget` วาด price และ durable Fill markers บนพื้นที่เต็มโดยไม่มี volume geometry

**Tech Stack:** Python 3.12+, dataclasses, Decimal, PySide6/QPainter, pytest, pytest-qt, Ruff, mypy

## Global Constraints

- ไม่มี field หรือ attribute `Candle.volume`
- replay CSV header ต้องเป็น `open_time,open,high,low,close` เท่านั้น และไม่รองรับรูปแบบเดิม
- Binance public payload อาจมี volume ตาม protocol แต่ต้องไม่แปลงหรือเก็บใน `Candle`
- Paper replay จาก fixture ราคาเดิมต้องให้ JSON เดิม
- ห้ามเปลี่ยน Strategy, execution, persistence, lifecycle หรือ Trading Safety behavior
- ใช้ Paper Trading และ fake adapters ใน tests; ห้ามส่ง Live order หรือใช้ Live credentials
- ใช้ชื่อไฟล์ module class function variable และ code comments เป็นภาษาอังกฤษ

---

## File Structure

- `src/tiewtrade/ui/candlestick_chart.py` — วาด candlestick และ durable Fill markers โดยไม่มี Volume panel
- `src/tiewtrade/market_data/candle.py` — นิยามและ validate immutable OHLC `Candle`
- `src/tiewtrade/integrations/binance/kline_parser.py` — แปลง Binance REST/WebSocket payload เป็น OHLC `Candle` และทิ้ง transport volume
- `src/tiewtrade/replay/csv_candles.py` — อ่าน deterministic OHLC CSV ห้าคอลัมน์
- `tests/fixtures/btcusdt_5m_tracer.csv` — fixture OHLC สำหรับ acceptance replay
- `tests/unit/ui/test_candlestick_chart.py` — contract ของ price-only painter model
- `tests/unit/market_data/test_completed_candle_stream.py` — contract และ validation ของ OHLC `Candle`
- `tests/unit/integrations/binance/test_kline_parser.py` — contract การทิ้ง Binance transport volume
- `tests/unit/replay/test_csv_candles.py` — contract CSV ห้าคอลัมน์และ error messages
- `tests/**` — call sites/helpers ที่สร้าง `Candle`; ลบ keyword `volume` โดยไม่เปลี่ยนราคาและเวลา
- `docs/superpowers/specs/2026-07-23-deterministic-csv-replay-design.md` — เปลี่ยน replay contract จาก OHLCV เป็น OHLC
- `docs/superpowers/specs/2026-07-26-public-binance-market-data-runtime-design.md` — เปลี่ยน Candle validation contract เป็น OHLC
- `docs/superpowers/specs/2026-07-30-dev-100-structured-market-data-logging-design.md` — แยกคำว่า Binance payload volume ออกจาก application Candle
- `docs/superpowers/specs/2026-08-02-dark-candlestick-chart-design.md` — เปลี่ยน chart scope เป็น OHLC และไม่มี Volume panel

---

### Task 1: ลบ Volume Geometry ออกจาก Candlestick Chart

**Files:**
- Modify: `tests/unit/ui/test_candlestick_chart.py`
- Modify: `src/tiewtrade/ui/candlestick_chart.py`

**Interfaces:**
- Consumes: `ChartSnapshot.candles: tuple[Candle, ...]`, `ChartSnapshot.markers: tuple[ChartMarker, ...]`
- Produces: `_build_painter_model(snapshot: ChartSnapshot, surface: QRectF) -> ChartPainterModel` ที่มีเฉพาะ `grid_lines`, `candles`, `markers`

- [ ] **Step 1: เขียน failing painter-contract test**

แทนที่ test ที่ตรวจ scaled volume ด้วย test ที่ตรวจว่า model ไม่มี volume geometry และ price ใช้ความสูงเต็ม:

```python
from dataclasses import fields


def test_painter_model_contains_only_price_candles_and_fill_markers() -> None:
    snapshot = ready_snapshot(
        candles=(
            candle(0, open_price="100", close_price="103"),
            candle(5, open_price="103", close_price="101"),
            candle(10, open_price="101", close_price="104"),
        )
    )

    model = chart_module._build_painter_model(snapshot, QRectF(0, 0, 300, 200))

    assert [field.name for field in fields(chart_module.CandleGlyph)] == [
        "wick",
        "body",
        "color",
    ]
    assert [field.name for field in fields(chart_module.ChartPainterModel)] == [
        "grid_lines",
        "candles",
        "markers",
    ]
    assert min(min(glyph.wick.y1(), glyph.wick.y2()) for glyph in model.candles) == 0
    assert max(max(glyph.wick.y1(), glyph.wick.y2()) for glyph in model.candles) == 200
    buy, sell = model.markers
    assert buy.label == "Buy"
    assert buy.triangle[0].y() < buy.triangle[1].y()
    assert sell.label == "Sell"
    assert sell.triangle[0].y() > sell.triangle[1].y()
    assert all(glyph.body.width() > 0 for glyph in model.candles)
```

ลบ parameter `volume` จาก helper `candle()` ในไฟล์เดียวกัน แต่ยังส่งค่า
`volume=Decimal("1")` คงที่เข้า `Candle` ชั่วคราวจน Task 2 เปลี่ยน domain contract

- [ ] **Step 2: รัน test เพื่อยืนยัน RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py::test_painter_model_contains_only_price_candles_and_fill_markers -q
```

Expected: FAIL เพราะ `CandleGlyph` ยังมี `volume_bar`, `ChartPainterModel` ยังมี
`volume_divider` และ price surface สูงเพียง 65% ของ chart surface

- [ ] **Step 3: เขียน implementation ขั้นต่ำของ price-only painter model**

เปลี่ยน dataclasses เป็น:

```python
@dataclass(frozen=True, slots=True)
class CandleGlyph:
    wick: QLineF
    body: QRectF
    color: QColor


@dataclass(frozen=True, slots=True)
class ChartPainterModel:
    grid_lines: tuple[QLineF, ...]
    candles: tuple[CandleGlyph, ...]
    markers: tuple[MarkerGlyph, ...]
```

ใน `_draw_chart()` ลบ `painter.drawLine(model.volume_divider)` และ
`painter.fillRect(candle.volume_bar, candle.color)`

ใน `_build_painter_model()` ใช้พื้นที่เต็ม:

```python
price_surface = surface
```

ลบ `volume_surface`, `max_volume`, `volume_height`, argument `volume_bar` และ
`volume_divider` จาก model constructor โดยคง wick/body, grid และ marker calculations เดิม

- [ ] **Step 4: รัน widget tests เพื่อยืนยัน GREEN**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -q
```

Expected: PASS ทุก test และไม่มี Volume geometry ใน painter model

- [ ] **Step 5: Commit chart slice**

```bash
git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py
git commit -m "refactor: remove chart volume panel"
```

---

### Task 2: เปลี่ยน Candle และ Input Boundaries เป็น OHLC

**Files:**
- Modify: `src/tiewtrade/market_data/candle.py`
- Modify: `src/tiewtrade/integrations/binance/kline_parser.py`
- Modify: `src/tiewtrade/replay/csv_candles.py`
- Modify: `tests/unit/market_data/test_completed_candle_stream.py`
- Modify: `tests/unit/integrations/binance/test_kline_parser.py`
- Modify: `tests/unit/replay/test_csv_candles.py`
- Modify: `tests/fixtures/btcusdt_5m_tracer.csv`
- Modify: `tests/acceptance/test_desktop_chart.py`
- Modify: `tests/acceptance/test_desktop_session_setup.py`
- Modify: `tests/acceptance/test_paper_futures_session.py`
- Modify: `tests/acceptance/test_paper_futures_trade_history.py`
- Modify: `tests/acceptance/test_public_market_data_runtime.py`
- Modify: `tests/support/paper_trade_history_acceptance.py`
- Modify: `tests/unit/application/test_chart_data.py`
- Modify: `tests/unit/application/test_chart_history.py`
- Modify: `tests/unit/application/test_paper_futures_session.py`
- Modify: `tests/unit/application/test_paper_runtime.py`
- Modify: `tests/unit/application/test_paper_spot_session.py`
- Modify: `tests/unit/execution/test_paper_futures.py`
- Modify: `tests/unit/execution/test_paper_spot.py`
- Modify: `tests/unit/integrations/sqlite/test_persistent_paper_futures_session.py`
- Modify: `tests/unit/integrations/sqlite/test_persistent_paper_spot_session.py`
- Modify: `tests/unit/integrations/sqlite/test_session_persistence.py`
- Modify: `tests/unit/market_data/test_candle_pipeline.py`
- Modify: `tests/unit/market_data/test_runtime.py`
- Modify: `tests/unit/replay/test_paper_spot_runner.py`
- Modify: `tests/unit/strategies/test_rsi_step_grid_indicators.py`
- Modify: `tests/unit/strategies/test_rsi_step_grid_strategy.py`
- Modify: `tests/unit/ui/test_candlestick_chart.py`
- Modify: `tests/unit/ui/test_chart_workflow.py`

**Interfaces:**
- Produces: `Candle(symbol: str, timeframe: str, open_time: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal)`
- Produces: `parse_rest_kline(payload: object, config: MarketDataConfig) -> Candle`
- Produces: `parse_websocket_kline(payload: object, config: MarketDataConfig) -> Candle | None`
- Produces: `CSV_HEADER = ("open_time", "open", "high", "low", "close")`
- Produces: `load_candles_csv(path: Path, config: MarketDataConfig) -> tuple[Candle, ...]`

- [ ] **Step 1: เขียน failing OHLC domain test**

เพิ่ม test ที่สร้าง `Candle` โดยไม่มี volume:

```python
def test_candle_is_an_ohlc_fact_without_volume() -> None:
    candle = Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
    )

    assert not hasattr(candle, "volume")
```

ลบ `volume` case ออกจาก `test_candle_rejects_invalid_market_data`; คง cases ของ
timeframe boundary และ OHLC range

- [ ] **Step 2: เขียน failing Binance boundary tests**

แก้ REST mapping test ให้ volume ที่ตำแหน่ง transport เป็นค่าที่แปลง Decimal ไม่ได้
แต่ parser ยังคืน OHLC Candle:

```python
def test_rest_kline_maps_ohlc_and_ignores_transport_volume() -> None:
    candle = parse_rest_kline(
        [1767225600000, "100.10", "102.20", "99.90", "101.30", object()],
        config(),
    )

    assert candle.open_time == datetime(2026, 1, 1, tzinfo=UTC)
    assert candle.open == Decimal("100.10")
    assert candle.high == Decimal("102.20")
    assert candle.low == Decimal("99.90")
    assert candle.close == Decimal("101.30")
    assert not hasattr(candle, "volume")
```

เพิ่ม WebSocket contract:

```python
def test_closed_websocket_kline_ignores_transport_volume() -> None:
    payload = closed_kline_payload()
    kline = payload["k"]
    assert isinstance(kline, dict)
    kline["v"] = object()

    candle = parse_websocket_kline(payload, config())

    assert candle is not None
    assert candle.close == Decimal("101.30")
    assert not hasattr(candle, "volume")
```

เพิ่ม REST shape test ให้ payload สี่ field ถูกปฏิเสธด้วย cause
`REST kline payload must contain at least 5 fields`; payload ห้า fieldต้องอ่านได้

- [ ] **Step 3: เขียน failing replay CSV contract tests**

เปลี่ยน happy path เป็น:

```python
path = write_csv(
    tmp_path,
    "open_time,open,high,low,close\n"
    "2026-01-01T00:00:00+00:00,100.10,102.20,99.90,101.00\n",
)
```

ยืนยัน `open` precision เหมือนเดิมและ `not hasattr(candles[0], "volume")`

เปลี่ยน invalid header matrix ให้รวม header เดิม:

```python
[
    "open_time,open,high,low,close,volume",
    "open_time,open,high,low",
    "open,open_time,high,low,close",
]
```

Error ใหม่ต้อง match `CSV header must be open_time,open,high,low,close`
เปลี่ยน non-finite parametrization เป็น `open`, `high`, `low`, `close` และข้อความ
`OHLC values must be finite`; row ที่มีหก field ต้อง fail ด้วย `expected 5 columns`

- [ ] **Step 4: รัน contract tests เพื่อยืนยัน RED**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/market_data/test_completed_candle_stream.py \
  tests/unit/integrations/binance/test_kline_parser.py \
  tests/unit/replay/test_csv_candles.py -q
```

Expected: FAIL เพราะ `Candle` ยังบังคับ `volume`, Binance parser ยัง validate volume
และ CSV reader ยังบังคับหกคอลัมน์

- [ ] **Step 5: Implement immutable OHLC Candle**

เปลี่ยน field list ใน `Candle` เป็น:

```python
@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
```

ลบ block นี้ออกทั้งหมด:

```python
if self.volume < 0:
    raise ValueError("volume must not be negative")
```

คง UTC, alignment, positive OHLC, range และ `close_time` behavior เดิม

- [ ] **Step 6: Implement Binance transport-volume discard**

ใน REST parser ใช้ `len(payload) < 5`, error `at least 5 fields`, `payload[:5]` และ
field names ห้าค่า ใน WebSocket parser ไม่ส่ง `kline.get("v")` เข้า helper

เปลี่ยน helper signature และ constructor เป็น:

```python
def _candle_from_values(
    values: Sequence[object],
    config: MarketDataConfig,
    *,
    field_names: tuple[str, str, str, str, str],
) -> Candle:
    open_time = _utc_datetime(
        _required_milliseconds(values[0], name=field_names[0]), name=field_names[0]
    )
    return Candle(
        symbol=config.symbol,
        timeframe=config.timeframe,
        open_time=open_time,
        open=_decimal_from_string(values[1], name=field_names[1]),
        high=_decimal_from_string(values[2], name=field_names[2]),
        low=_decimal_from_string(values[3], name=field_names[3]),
        close=_decimal_from_string(values[4], name=field_names[4]),
    )
```

- [ ] **Step 7: Implement five-column replay reader**

ใช้ contract ต่อไปนี้:

```python
CSV_HEADER = ("open_time", "open", "high", "low", "close")
```

Header error ต้องเป็น `CSV header must be open_time,open,high,low,close`, row error
ต้องเป็น `expected 5 columns`, unpack เฉพาะ `open_price, high, low, close`, finite
error ต้องเป็น `OHLC values must be finite` และสร้าง `Candle` โดยไม่มี volume

- [ ] **Step 8: ลบ volume จาก Candle call sites และ fixture**

ในทุก test/helper ที่ระบุใน Files ของ Task นี้ ลบเฉพาะ keyword argument รูปแบบ:

```python
volume=Decimal("..."),
```

ห้ามแก้ OHLC, symbol, timeframe หรือ timestamp พร้อมกัน ใน
`tests/unit/ui/test_candlestick_chart.py` ลบ `volume` คงที่ที่เหลือจาก Task 1

ใน `tests/fixtures/btcusdt_5m_tracer.csv` เปลี่ยน header เป็นห้าคอลัมน์และลบ field
ที่หกจากข้อมูลทั้ง 40 แถว โดยให้แถวแรกเป็น:

```text
open_time,open,high,low,close
2026-01-01T00:00:00Z,101,102,99,100
```

คง tests ที่มี string `"volume"` ใน forbidden logging-field assertions ไว้ เพราะ
ยังป้องกันไม่ให้ transport payload facts หลุดเข้า structured logs

- [ ] **Step 9: รัน focused tests เพื่อยืนยัน GREEN**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/market_data/test_completed_candle_stream.py \
  tests/unit/integrations/binance/test_kline_parser.py \
  tests/unit/replay/test_csv_candles.py \
  tests/unit/ui/test_candlestick_chart.py \
  tests/acceptance/test_paper_replay_cli.py -q
```

Expected: PASS และ replay CLI ยังคืน
`{"accepted_candles":40,"closed_baskets":1,"current_entries":0,"realized_pnl":"13.84062222"}`

- [ ] **Step 10: ตรวจว่า production ไม่มี Candle volume consumer เหลือ**

Run:

```bash
rg -n "\.volume\b|volume=" src tests
```

Expected: ไม่พบ `Candle.volume` หรือ `volume=`; อนุญาตเฉพาะ string `"volume"` ใน
forbidden logging-field assertions และ Binance payload fixtures ที่จำลอง field `v`

- [ ] **Step 11: Commit OHLC domain slice**

```bash
git add src/tiewtrade/market_data/candle.py \
  src/tiewtrade/integrations/binance/kline_parser.py \
  src/tiewtrade/replay/csv_candles.py tests
git commit -m "refactor: make candle data OHLC only"
```

---

### Task 3: ปรับ Source of Truth ให้เป็น OHLC

**Files:**
- Modify: `docs/superpowers/specs/2026-07-23-deterministic-csv-replay-design.md`
- Modify: `docs/superpowers/specs/2026-07-26-public-binance-market-data-runtime-design.md`
- Modify: `docs/superpowers/specs/2026-07-30-dev-100-structured-market-data-logging-design.md`
- Modify: `docs/superpowers/specs/2026-08-02-dark-candlestick-chart-design.md`

**Interfaces:**
- Consumes: OHLC `Candle` และ five-column replay contract จาก Task 2
- Produces: เอกสารที่ไม่อธิบาย application Candle หรือ replay format เป็น OHLCV

- [ ] **Step 1: แก้ deterministic replay design**

เปลี่ยน header ทุกจุดเป็น `open_time,open,high,low,close`, เปลี่ยน OHLCV เป็น OHLC,
ลบ volume validation และระบุว่า finite validation ครอบคลุม `open`, `high`, `low`,
`close` เท่านั้น

- [ ] **Step 2: แก้ public runtime และ chart designs**

ใน public runtime design เปลี่ยนข้อ validation เป็น:

```text
CompletedCandlePipeline validates symbol, timeframe, UTC alignment, and OHLC.
```

ใน dark chart design เปลี่ยน scope เป็น completed OHLC candles และระบุว่า price
surface ไม่มี Volume panel

- [ ] **Step 3: ทำ logging terminology ให้ชัด**

เปลี่ยน `Candle OHLC หรือ volume` เป็น `Candle OHLC หรือ Binance payload volume`
ทั้ง Constraints และ Success Criteria เพื่อรักษากฎห้าม log transport volume โดยไม่
อ้างว่า volume เป็น field ของ `Candle`

- [ ] **Step 4: ตรวจคำที่ขัดกับ contract ใหม่**

Run:

```bash
rg -n "OHLCV|open_time,open,high,low,close,volume|Candle.*volume|volume.*Candle" \
  docs/superpowers/specs README.md PRODUCT.md CONTEXT.md ARCHITECTURE.md PROJECT_PLAN.md
```

Expected: ไม่พบข้อความที่อธิบาย application `Candle` หรือ replay CSV เป็น OHLCV;
ข้อความเกี่ยวกับ Binance payload volume และ spec ฉบับ removal ใช้ได้เมื่อบริบทชัดเจน

- [ ] **Step 5: Commit documentation alignment**

```bash
git add docs/superpowers/specs/2026-07-23-deterministic-csv-replay-design.md \
  docs/superpowers/specs/2026-07-26-public-binance-market-data-runtime-design.md \
  docs/superpowers/specs/2026-07-30-dev-100-structured-market-data-logging-design.md \
  docs/superpowers/specs/2026-08-02-dark-candlestick-chart-design.md
git commit -m "docs: align market data terminology with OHLC"
```

---

### Task 4: Full Verification

**Files:**
- Verify only: `src/tiewtrade/`
- Verify only: `tests/`
- Verify only: working-tree and committed range

**Interfaces:**
- Consumes: price-only chart จาก Task 1, OHLC domain/input boundaries จาก Task 2 และ aligned documentation จาก Task 3
- Produces: verification evidence ก่อนรายงาน completion

- [ ] **Step 1: รัน full test suite**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Expected: PASS ทุก unit, integration และ acceptance test โดยไม่มี network หรือ Live side effect

- [ ] **Step 2: รัน lint และ format checks**

Run:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
```

Expected: ทั้งสองคำสั่ง exit 0

- [ ] **Step 3: รัน strict type checking**

Run:

```bash
.venv/bin/python -m mypy
```

Expected: `Success: no issues found`

- [ ] **Step 4: ตรวจ whitespace และ final diff**

Run:

```bash
git diff --check
git diff --check 5bf3836 HEAD
git status --short
```

Expected: `git diff --check` ทั้งสองคำสั่งไม่มี output และ status ไม่มีไฟล์ที่ไม่ได้ตั้งใจ

- [ ] **Step 5: ตรวจ acceptance criteria จาก source**

Run:

```bash
rg -n "\.volume\b|volume=" src tests
rg -n "volume_bar|volume_divider|max_volume|volume_surface" src tests
head -1 tests/fixtures/btcusdt_5m_tracer.csv
```

Expected: สองคำสั่ง `rg` ไม่พบ production/test Candle volume หรือ chart volume
geometry และ fixture header เป็น `open_time,open,high,low,close`

หาก verification พบ regression ให้กลับไปยัง Task เจ้าของ behavior นั้น เขียน failing
regression test ก่อนแก้ implementation แล้วรัน Task 4 ใหม่ทั้งหมด ห้ามสร้าง empty
verification commit เมื่อ checks ผ่านโดยไม่มีการเปลี่ยนไฟล์
