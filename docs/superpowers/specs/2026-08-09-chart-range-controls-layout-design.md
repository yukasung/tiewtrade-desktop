# Chart Range Controls Layout Design

## เป้าหมาย

ทำให้การควบคุมช่วงข้อมูลบน `CandlestickChartWidget` อ่านง่าย กดง่าย และไม่ซ้อนทับกับช่วงเวลา หรือราคาในกราฟ โดยยังคงพฤติกรรมการเปลี่ยนช่วงข้อมูลเดิมทั้งหมด

## ขอบเขต

- เปลี่ยนข้อความปุ่ม `Previous` และ `Next` เป็นลูกศร `←` และ `→`
- จัดกลุ่มด้านขวาของ header เป็นช่วงเวลา ตามด้วยปุ่มก่อนหน้าและปุ่มถัดไป
- เพิ่มจังหวะ spacing ระหว่างช่วงเวลาและปุ่ม 12px และระหว่างปุ่ม 8px
- กำหนดความกว้างปุ่ม 44px เพื่อให้ hit target คงที่
- คำนวณพื้นที่วาดกราฟจากขอบล่างจริงของ header แล้วเว้นช่องว่าง 24px ก่อนวาด grid, candle และ price axis

## การออกแบบ

`CandlestickChartWidget` เก็บ reference ของ header layout และจัดกลุ่ม controls ด้านขวาใน nested `QHBoxLayout` เพื่อให้ Qt เป็นผู้จัดสรรพื้นที่ระหว่าง `chartRange`, `previousRangeButton` และ `nextRangeButton` โดยไม่พึ่งตำแหน่งวาดแบบ hard-coded

ราคาที่แกนขวาและข้อมูลกราฟจะเริ่มใต้ header ที่คำนวณได้จริง 24px ไม่ใช้ค่า top offset คงที่อีกต่อไป จึงไม่ซ้อนกับปุ่มเมื่อ theme, font metrics หรือความสูงปุ่มเปลี่ยนไป ปุ่มยังคงกว้าง 44px เพื่อให้กดได้สะดวก

ปุ่มยังคง `accessibleName` เดิม (`Previous chart range`, `Next chart range`) และ keyboard behavior เดิม เพื่อไม่เปลี่ยน accessibility contract หรือ `range_requested` semantics

## การทดสอบ

- เพิ่ม regression test ที่ render chart ขนาดกว้าง พร้อม snapshot แล้วตรวจว่า control header, ปุ่มทั้งสอง และ plot surface ไม่ตัดกัน
- ยืนยันว่าปุ่มมีข้อความลูกศร, ความกว้าง 44px และ accessibility name เดิม
- รัน unit/integration tests ทั้งหมด รวมถึง Ruff, format, mypy และ `git diff --check`

## ข้อยกเว้น

งานนี้ไม่เปลี่ยน market data, candle content, range navigation logic, current-price label, crosshair หรือ chart painter geometry นอกเหนือจากขอบบนของ plot surface
