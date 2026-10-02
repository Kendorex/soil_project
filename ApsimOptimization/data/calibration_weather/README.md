# Погода для калибровки сорта Zlata

Основным источником калибровочной погоды является файл Open-Meteo, переданный
коллегой:

`open_meteo_vniimz_2004_2024_source.csv`

Метаданные источника:

- координаты: 56.836555° с. ш., 35.924370° в. д.;
- высота: 134 м;
- часовой пояс: Europe/Moscow, UTC+3;
- полный непрерывный период: 01.01.2004–31.12.2023;
- дополнительная строка: 01.01.2024;
- пропущенных суток и значений нет.

Для калибровки Zlata сформированы отдельные APSIM-файлы
`weather_zlata_2020.met`–`weather_zlata_2023.met`.

| APSIM | Open-Meteo | Единицы APSIM |
|---|---|---|
| `radn` | `shortwave_radiation_sum` | МДж/м²/сутки |
| `maxt` | `temperature_2m_max` | °C |
| `mint` | `temperature_2m_min` | °C |
| `rain` | `precipitation_sum` | мм/сутки |
| `wind_speed` | `wind_speed_10m_mean` | м/с |
| `sp` | `surface_pressure_mean` | Па; исходные гПа умножены на 100 |

Импорт воспроизводится командой:

```bash
python3 scripts/import_open_meteo_calibration_weather.py \
  /path/to/open-meteo-56.84N35.92E134m.csv
```

Предыдущие NASA POWER файлы сохранены как
`weather_zlata_YEAR_nasa_power.met`, а исходный ответ NASA — как
`nasa_power_2020_2023_raw.csv`. Они больше не являются основным входом
калибровки.

Файлы четырёх климатических сценариев в `data/weather/` не изменены. Замена
источника относится только к исторической калибровке сорта.
