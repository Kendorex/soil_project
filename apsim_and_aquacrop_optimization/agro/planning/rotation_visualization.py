"""Readable text and PNG summaries for a selected crop-rotation map."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch


CATEGORY_STYLE = {
    "perennial_grass": ("Многолетние травы", "#7FB36A"),
    "oats_with_underseed": ("Овес с подсевом", "#69A9D3"),
    "winter_rye": ("Озимая рожь", "#D39A45"),
    "spring_wheat": ("Яровая пшеница", "#E8C547"),
    "legume_cereal_mixture": ("Вико-овсяная смесь", "#9A78C5"),
    "mustard": ("Горчица", "#D8A7C7"),
    "rape": ("Рапс", "#70B7A5"),
    "potato": ("Картофель", "#A87555"),
}

SHORT_LABEL = {
    "perennial_grass_year1": "Травы\n1-й год",
    "perennial_grass_year2": "Травы\n2-й год",
    "perennial_grass_year3": "Травы\n3-й год",
    "oats_with_underseed": "Овес +\nподсев",
    "winter_rye": "Озимая\nрожь",
    "spring_wheat": "Яровая\nпшеница",
    "legume_cereal_mixture": "Вико-\nовсяная",
    "mustard": "Горчица",
    "rape": "Рапс",
    "potato": "Картофель",
}


def _field_number(field: str) -> tuple[int, str]:
    suffix = field.removeprefix("Field")
    return (int(suffix), field) if suffix.isdigit() else (10**9, field)


def _selected(field_result: dict) -> dict:
    return field_result.get("selected_alternative", field_result["alternatives"][0])


def _state_categories(result: dict) -> dict[str, str]:
    categories = result.get("farm_selection", {}).get("categories", {})
    return {
        state_id: category
        for category, spec in categories.items()
        for state_id in spec.get("states", [])
    }


def write_rotation_text_report(path: str | Path, result: dict) -> None:
    path = Path(path)
    start_year = int(result["start_year"])
    end_year = int(result["end_year"])
    years = list(range(start_year, end_year + 1))
    lines = [
        f"КАРТА СЕВООБОРОТА {start_year}–{end_year}",
        "",
        "Статус: карта совместимых переходов; APSIM не выполнялся.",
        "С — начальное состояние; предшественник до периода неизвестен и переход не оценён.",
        "Ф — переход зафиксирован по фактической книге и не оценён матрицей.",
        "И — переход подтверждён историей хозяйства и не оценён матрицей.",
        "Ц — целесообразный матричный переход.",
        "П — продолжение существующего многолетнего травостоя.",
        "",
        "ПО ПОЛЯМ",
    ]
    for field, field_result in sorted(result["fields"].items(), key=lambda item: _field_number(item[0])):
        chosen = _selected(field_result)
        plan = " | ".join(
            f"{year}: {crop}"
            for year, crop in zip(years, chosen["sequence_names"])
        )
        mean_score = chosen.get("mean_score")
        mean_text = (
            f"{mean_score:.3f}" if mean_score is not None
            else "нет матричных переходов в горизонте"
        )
        predecessor_label = (
            f"до {start_year}: не задан"
            if result.get("first_year_transition_scored") is False
            else f"до {start_year}: {chosen['initial_crop_name']}"
        )
        lines.extend([
            f"{field} ({predecessor_label})",
            f"  {plan}",
            f"  Коды: {', '.join(chosen['transition_codes'])}; "
            f"средний балл: {mean_text}",
        ])

    farm = result.get("farm_selection")
    if farm:
        lines.extend(["", "ПО ГОДАМ"])
        for counts in farm["annual_counts"]:
            values = []
            for category, count in counts.items():
                if category == "year":
                    continue
                label = CATEGORY_STYLE.get(category, (category, ""))[0]
                values.append(f"{label}: {count}")
            lines.append(f"{counts['year']}: " + "; ".join(values))
        lines.extend([
            "",
            "ПРОВЕРКА ИСТОРИЧЕСКИХ ДИАПАЗОНОВ",
            f"Соблюдены полностью: {'да' if farm['within_historical_ranges'] else 'нет'}",
            f"Суммарное превышение/недобор: {farm['range_violation_units']}",
        ])
        if not farm["within_historical_ranges"]:
            lines.append(
                "Отклонения сохранены явно; переходы Ц не заменялись на Д ради совпадения площадей."
            )

    lines.extend([
        "",
        "ОГРАНИЧЕНИЕ",
        "Карта не является прогнозом урожая, прибыли или состояния почвы до многолетней проверки APSIM.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_rotation_map(path: str | Path, result: dict) -> None:
    path = Path(path)
    fields = sorted(result["fields"], key=_field_number)
    years = list(range(int(result["start_year"]), int(result["end_year"]) + 1))
    state_to_category = _state_categories(result)
    present_categories = []
    state_rows = []
    for field in fields:
        states = _selected(result["fields"][field])["sequence_ids"]
        state_rows.append(states)
        for state_id in states:
            category = state_to_category.get(state_id, state_id)
            if category not in present_categories:
                present_categories.append(category)

    colors = [CATEGORY_STYLE.get(category, (category, "#B8B8B8"))[1] for category in present_categories]
    category_index = {category: index for index, category in enumerate(present_categories)}
    values = np.array([
        [category_index[state_to_category.get(state_id, state_id)] for state_id in row]
        for row in state_rows
    ])

    fig, ax = plt.subplots(figsize=(12, 6.8))
    ax.imshow(values, cmap=ListedColormap(colors), aspect="auto", vmin=-0.5, vmax=len(colors) - 0.5)
    ax.set_xticks(range(len(years)), years)
    ax.set_yticks(range(len(fields)), fields)
    ax.set_xlabel("Год")
    ax.set_ylabel("Поле")
    ax.set_title(f"Карта севооборота {years[0]}–{years[-1]}", fontweight="bold", pad=14)
    ax.set_xticks(np.arange(-0.5, len(years), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(fields), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=2)
    ax.tick_params(which="minor", bottom=False, left=False)

    for row_index, states in enumerate(state_rows):
        for column_index, state_id in enumerate(states):
            ax.text(
                column_index, row_index, SHORT_LABEL.get(state_id, state_id),
                ha="center", va="center", fontsize=8.5, color="#1F1F1F",
            )

    legend = [
        Patch(facecolor=CATEGORY_STYLE.get(category, (category, "#B8B8B8"))[1],
              label=CATEGORY_STYLE.get(category, (category, ""))[0])
        for category in present_categories
    ]
    ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=3, frameon=False)
    farm = result.get("farm_selection")
    if farm and not farm["within_historical_ranges"]:
        fig.text(
            0.5, 0.015,
            f"Исторические диапазоны соблюдены не полностью: отклонение {farm['range_violation_units']}",
            ha="center", color="#8A3B12", fontsize=9,
        )
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
