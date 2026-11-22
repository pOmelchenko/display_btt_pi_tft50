# Проверка единого оверлея TFT50

22 ноября 2026 года на Linux x86-64 выполнено сравнение итоговых деревьев
при помощи dtc 1.7.2 и fdtoverlay. Базовый DTB взят из пакета ядра Radxa
`7.0.11-7-qcom`. SHA-256 DTB и обоих прежних DTBO совпали с
[аппаратным протоколом](btt-pi-tft50-v2.1-2026-10-15.json).

| Вход | SHA-256 |
| --- | --- |
| DTB Q6A | `647d22e8f8aa5789b65339b7b80412c355a6bfbb6723e2023f6564df55252549` |
| Прежний display DTBO | `f0015e8bd11de17b5c9852eaf6ada3ed4263f406880999b635e14b63327663c7` |
| Прежний power DTBO | `65521494b0efda73ccdcc5a790a40bc5c746efbe1f38cf35ce40fb0a8843b2a8` |
| Единый DTBO | `8b218b826c1363b2187b9a424bfa69ef555b29c1b4acede1557b6cf599f0e8ec` |

Для каждого набора камер одинаковый базовый DTB загружался с двумя
прежними оверлеями и с одним новым. Полный текст `dtc -s -I dtb -O dts`
совпал побайтово, включая phandle, связи DSI и узлы камер.

| Дополнительные оверлеи | SHA-256 общего нормализованного DTS |
| --- | --- |
| Без камер | `5b0d1023ef8005222fe562fd3ad711bc0746fc494b60592d78efc344de34862d` |
| CAM1 IMX296, CAM2 IMX219, CAM3 IMX708 | `6d07e83c9f50f638a4798e2ed9fa708e22e60c49786f305133c013eb1d148478` |

Профиль IMX296 взят из [camera_imx296](https://github.com/pOmelchenko/camera_imx296/tree/d35dee42928c7d77df124662b96f36157019e93a).
Штатные профили CAM2 и CAM3 взяты из [radxa-overlays](https://github.com/radxa-pkg/radxa-overlays/tree/8fce109f183455d95dc13d6ad7071ab28f55be5f).

Повторить проверку из корня репозитория с копиями файлов своей установки:

```sh
python3 tests/compare_overlays.py /path/to/q6a.dtb \
    /path/to/q6a-btt-tft50-display.dtbo /path/to/q6a-btt-tft50-power.dtbo
```

Аргументами после power DTBO можно передать файлы камер в порядке загрузки.
Инструмент сверяет прежние DTBO с контрольными суммами и компилирует новый
из [исходника](../../../target/sbc/radxa_dragon_q6a/q6a-btt-pi-tft50-v21.dts).
Единый профиль основан на [исходнике Radxa, BSD-3-Clause](https://github.com/radxa-pkg/radxa-overlays/blob/fbcee0ab93d04b07df0ed153b0ce68005c460e31/arch/arm64/boot/dts/qcom/overlays/qcs6490-radxa-dragon-q6a-raspberry-pi-7-inch-touchscreen.dtso).
Аппаратный результат для прежних оверлеев зафиксирован в [PR платы](https://github.com/pOmelchenko/radxa_dragon_q6a/pull/1).
Этот запуск проверял Device Tree. Загрузка платы с единым DTBO и проверка касаний после неё ещё предстоят.
