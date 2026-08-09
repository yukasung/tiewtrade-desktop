# Centered Table Empty States Design

## Goal

แสดง empty state ไว้กึ่งกลางพื้นที่ข้อมูลของตารางเหมือน Binance โดยไม่ทับหัวคอลัมน์และไม่ทำให้ layout ของตารางเลื่อน

## Scope

- ใช้กับ `Position / Basket`, `Basket History` และ `Trade Fills`
- Center เฉพาะข้อความ empty: `No open Position or Basket`, `No trade history` และ `No fills for this Basket`
- ซ่อนข้อความทันทีเมื่อ table มี row จริง

## Layout

- Table และ empty label อยู่ใน layered container เดียวกัน
- Empty label align กึ่งกลางของ table viewport; header ของ table ยังคงแสดงและไม่ถูกทับ
- Overlay ไม่รับ mouse event เพื่อไม่กีดขวาง scroll, selection หรือ column controls ของ table
- ไม่สร้าง vertical layout ที่วาง state label เหนือ table อีกต่อไปสำหรับ empty state

## Non-goals

- ไม่ย้าย Loading, Error หรือ Stale messages; คงตำแหน่งเดิมเพื่อให้สถานะผิดปกติชัดเจน
- ไม่เปลี่ยน query, pagination, selection, fill loading หรือ persistence

## Verification

- Widget tests ตรวจ empty label อยู่ใน viewport body, อยู่กึ่งกลาง และไม่ทับ horizontal header
- Widget tests ตรวจ label ซ่อนเมื่อมี row
- รัน focused UI tests, full pytest, Ruff, format check, mypy และ `git diff --check`
