# EDT FT5x06 для Q6A

Сохранён исходник `drivers/input/touchscreen/edt-ft5x06.c` из Linux stable
v7.0.11. Комплект собирает его для `7.0.11-7-qcom`. SHA-256 исходника,
патчей, промежуточных результатов и лицензии записаны в [source.lock.json](source.lock.json).

| Файл | Происхождение |
| --- | --- |
| [edt-ft5x06-stock.c](edt-ft5x06-stock.c) | [Linux stable v7.0.11](https://github.com/gregkh/linux/blob/v7.0.11/drivers/input/touchscreen/edt-ft5x06.c) |
| [0001-input-polling.patch](0001-input-polling.patch) | [Marek Vasut, предложение для Linux input](https://lore.kernel.org/all/20260109035149.1341931-2-marex@nabladev.com/). Опрос 17 мс в отсутствие IRQ. |
| [0002-pi-touch-packets.patch](0002-pi-touch-packets.patch) | Адаптация обработки пакетов Raspberry Pi на основе изменений Dave Stevenson и Nick Hollinghurst. [Закреплённая версия](https://github.com/raspberrypi/linux/blob/d2afc067c8adfcd6563533ff30cadb41e169aa23/drivers/input/touchscreen/edt-ft5x06.c) |
| [GPL-2.0](GPL-2.0) | Текст лицензии драйвера и производных патчей из Linux. |

Оба патча перенесены побайтово из [PR платы](https://github.com/pOmelchenko/radxa_dragon_q6a/pull/1).
Драйвер обрабатывает только активные точки из TD_STATUS и отпускает исчезнувшие контакты.
