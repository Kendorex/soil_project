"""
Интеграционные тесты BMI-обёртки над APSIM + PINN.

Запуск (из корня проекта):
    pytest tests/test_bmi_apsim_pinn.py -v -s
    pytest tests/test_bmi_apsim_pinn.py -v -s -m integration   # только интеграционные
    pytest tests/test_bmi_apsim_pinn.py -v -s -m "not integration"  # только быстрые

ВАЖНО: интеграционные тесты требуют:
    - WSL с установленным APSIM
    - запущенного APSIM (или возможности его запустить)
    - рабочий .apsimx (в config/)
    - рабочий .pth (может быть необученным — тесты этого не проверяют)
"""

import os
import sys
import json
import time
from pathlib import Path

import numpy as np
import pytest

# --- Настройка путей ---
# conftest.py уже добавил src в sys.path, но для надёжности добавим явно
BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# --- Импорт тестируемого класса ---
from bmi_apsim_pinn import (
    BmiApsimPinn,
    BMI_TO_APSIM,
    INPUT_VARS,
    OUTPUT_VARS,
    VAR_UNITS,
    VAR_TYPE,
    ARRAY_VARS,
    SCALAR_VARS,
)


# =========================================================
# КОНФИГУРАЦИЯ ТЕСТОВ
# =========================================================
CONFIG_PATH = BASE_DIR / "config" / "bmi_config.json"
N_LAYERS_EXPECTED = 7     # из вашего .apsimx
TIME_STEP_EXPECTED = 1.0


# =========================================================
# ФИКСТУРЫ
# =========================================================
@pytest.fixture(scope="module")
def bmi():
    """
    Один экземпляр BmiApsimPinn на весь модуль тестов.
    Инициализируется один раз, потом finalize().
    """
    if not CONFIG_PATH.exists():
        pytest.skip(f"Нет {CONFIG_PATH} — пропускаем интеграционные тесты")

    model = BmiApsimPinn()
    model.initialize(str(CONFIG_PATH))
    yield model
    try:
        model.finalize()
    except Exception as e:
        print(f"[fixture] finalize: {e}")


# =========================================================
# 1. ТЕСТЫ ИНИЦИАЛИЗАЦИИ
# =========================================================
@pytest.mark.integration
def test_initialize_completes(bmi):
    """initialize() не падает и помечает модель как инициализированную."""
    assert bmi._initialized is True


@pytest.mark.integration
def test_n_layers_matches_apsimx(bmi):
    """Число слоёв соответствует .apsimx."""
    assert bmi._n_layers == N_LAYERS_EXPECTED


@pytest.mark.integration
def test_time_bounds_positive(bmi):
    """start_time < end_time, оба неотрицательные."""
    assert bmi.get_start_time() >= 0.0
    assert bmi.get_end_time() > bmi.get_start_time()


# =========================================================
# 2. ТЕСТЫ ИНФОРМАЦИИ О МОДЕЛИ
# =========================================================
def test_get_component_name_is_str():
    """get_component_name возвращает непустую строку."""
    model = BmiApsimPinn()
    name = model.get_component_name()
    assert isinstance(name, str)
    assert len(name) > 0


def test_get_input_item_count_matches_list():
    """Число входных переменных совпадает с длиной INPUT_VARS."""
    model = BmiApsimPinn()
    assert model.get_input_item_count() == len(INPUT_VARS)


def test_get_output_item_count_matches_list():
    """Число выходных переменных совпадает с длиной OUTPUT_VARS."""
    model = BmiApsimPinn()
    assert model.get_output_item_count() == len(OUTPUT_VARS)


def test_get_input_var_names_returns_tuple_of_str():
    """Имена входных переменных — кортеж строк."""
    model = BmiApsimPinn()
    names = model.get_input_var_names()
    assert isinstance(names, tuple)
    assert all(isinstance(n, str) for n in names)
    assert set(names) == set(INPUT_VARS)


def test_get_output_var_names_returns_tuple_of_str():
    """Имена выходных переменных — кортеж строк."""
    model = BmiApsimPinn()
    names = model.get_output_var_names()
    assert isinstance(names, tuple)
    assert all(isinstance(n, str) for n in names)
    assert set(names) == set(OUTPUT_VARS)


def test_get_var_names_covers_all_mapping():
    """get_var_names возвращает все ключи BMI_TO_APSIM."""
    model = BmiApsimPinn()
    names = model.get_var_names()
    assert set(names) == set(BMI_TO_APSIM.keys())


# =========================================================
# 3. ТЕСТЫ ВРЕМЕНИ
# =========================================================
def test_time_units_is_string():
    """get_time_units возвращает строку."""
    model = BmiApsimPinn()
    units = model.get_time_units()
    assert isinstance(units, str)
    assert len(units) > 0


def test_time_step_positive():
    """get_time_step возвращает положительное число."""
    model = BmiApsimPinn()
    dt = model.get_time_step()
    assert isinstance(dt, (int, float))
    assert dt > 0.0


def test_time_step_matches_expected():
    """Шаг времени = 1 день."""
    model = BmiApsimPinn()
    assert model.get_time_step() == TIME_STEP_EXPECTED


# =========================================================
# 4. ТЕСТЫ МЕТАДАННЫХ ПЕРЕМЕННЫХ
# =========================================================
@pytest.mark.integration
def test_get_var_type_for_all_vars(bmi):
    """get_var_type работает для каждой переменной и возвращает str."""
    for name in bmi.get_var_names():
        t = bmi.get_var_type(name)
        assert isinstance(t, str)
        assert t in ("float64", "int64")


@pytest.mark.integration
def test_get_var_units_for_all_vars(bmi):
    """get_var_units работает для каждой переменной и совпадает с VAR_UNITS."""
    for name in bmi.get_var_names():
        u = bmi.get_var_units(name)
        assert isinstance(u, str)
        assert u == VAR_UNITS[name]


@pytest.mark.integration
def test_get_var_itemsize_positive(bmi):
    """get_var_itemsize > 0 для каждой переменной."""
    for name in bmi.get_var_names():
        s = bmi.get_var_itemsize(name)
        assert isinstance(s, int)
        assert s > 0


@pytest.mark.integration
def test_get_var_nbytes_matches_itemsize_times_size(bmi):
    """get_var_nbytes == itemsize * размер буфера."""
    for name in bmi.get_var_names():
        nbytes = bmi.get_var_nbytes(name)
        itemsize = bmi.get_var_itemsize(name)
        size = bmi._values[name].size
        assert nbytes == itemsize * size


@pytest.mark.integration
def test_get_var_location_is_node(bmi):
    """Все переменные — на узлах ('node')."""
    for name in bmi.get_var_names():
        assert bmi.get_var_location(name) == "node"


@pytest.mark.integration
def test_get_var_grid_returns_zero(bmi):
    """Все переменные привязаны к одной сетке (grid_id=0)."""
    for name in bmi.get_var_names():
        assert bmi.get_var_grid(name) == 0


# =========================================================
# 5. ТЕСТЫ СЕТКИ
# =========================================================
@pytest.mark.integration
def test_grid_rank_is_1(bmi):
    """Сетка одномерная."""
    assert bmi.get_grid_rank(0) == 1


@pytest.mark.integration
def test_grid_size_matches_n_layers(bmi):
    """Размер сетки == число слоёв."""
    assert bmi.get_grid_size(0) == bmi._n_layers


@pytest.mark.integration
def test_grid_type_is_rectilinear(bmi):
    """Сетка типа rectilinear (слои разной толщины)."""
    assert bmi.get_grid_type(0) == "rectilinear"


@pytest.mark.integration
def test_grid_shape_matches_n_layers(bmi):
    """shape == (n_layers,)."""
    shape = bmi.get_grid_shape(0)
    assert isinstance(shape, tuple)
    assert shape == (bmi._n_layers,)


@pytest.mark.integration
def test_grid_spacing_is_positive_array(bmi):
    """spacing — массив положительных толщин."""
    spacing = bmi.get_grid_spacing(0)
    arr = np.asarray(spacing)
    assert arr.ndim == 1
    assert arr.size == bmi._n_layers
    assert np.all(arr > 0)


@pytest.mark.integration
def test_grid_origin_is_zero(bmi):
    """origin = [0.0]."""
    origin = bmi.get_grid_origin(0)
    arr = np.asarray(origin)
    assert arr.shape == (1,)
    assert arr[0] == 0.0


@pytest.mark.integration
def test_grid_x_is_monotonic(bmi):
    """Координаты x (глубины) монотонно возрастают."""
    x = np.asarray(bmi.get_grid_x(0))
    assert x.ndim == 1
    assert x.size == bmi._n_layers
    assert np.all(np.diff(x) > 0)


@pytest.mark.integration
def test_grid_node_count_matches_n_layers(bmi):
    """node_count == n_layers."""
    assert bmi.get_grid_node_count(0) == bmi._n_layers


@pytest.mark.integration
def test_grid_y_z_are_empty_for_1d(bmi):
    """Для 1D сетки y и z пусты."""
    assert np.asarray(bmi.get_grid_y(0)).size == 0
    assert np.asarray(bmi.get_grid_z(0)).size == 0


@pytest.mark.integration
def test_grid_edge_face_counts_zero(bmi):
    """Для 1D сетки рёбер и граней нет."""
    assert bmi.get_grid_edge_count(0) == 0
    assert bmi.get_grid_face_count(0) == 0


@pytest.mark.integration
def test_grid_topology_arrays_empty(bmi):
    """Топологические массивы пусты для 1D."""
    assert np.asarray(bmi.get_grid_edge_nodes(0)).size == 0
    assert np.asarray(bmi.get_grid_face_edges(0)).size == 0
    assert np.asarray(bmi.get_grid_face_nodes(0)).size == 0
    assert np.asarray(bmi.get_grid_nodes_per_face(0)).size == 0


# =========================================================
# 6. ТЕСТЫ ЗНАЧЕНИЙ
# =========================================================
@pytest.mark.integration
def test_get_value_returns_copy(bmi):
    """get_value возвращает копию, а не ссылку."""
    for name in bmi.get_var_names():
        v = bmi.get_value(name)
        assert isinstance(v, np.ndarray)
        # копия: изменение не должно влиять на исходный буфер
        original = bmi._values[name].copy()
        v[:] = -999.0
        assert np.array_equal(bmi._values[name], original)


@pytest.mark.integration
def test_get_value_shapes_correct(bmi):
    """Формы буферов соответствуют ARRAY_VARS / SCALAR_VARS / date."""
    for name in ARRAY_VARS:
        assert bmi.get_value(name).shape == (bmi._n_layers,)
    for name in SCALAR_VARS:
        assert bmi.get_value(name).shape == (1,)
    assert bmi.get_value("date").shape == (1,)


@pytest.mark.integration
def test_get_value_dtypes_correct(bmi):
    """Типы данных соответствуют VAR_TYPE."""
    for name in bmi.get_var_names():
        v = bmi.get_value(name)
        expected = np.float64 if VAR_TYPE[name] == "float64" else np.int64
        assert v.dtype == expected


@pytest.mark.integration
def test_get_value_no_nan_after_first_step(bmi):
    """После первого шага значения не NaN."""
    for name in bmi.get_var_names():
        v = bmi.get_value(name)
        assert not np.any(np.isnan(v)), f"{name} содержит NaN"


@pytest.mark.integration
def test_get_value_ptr_returns_ctypes_pointer(bmi):
    """
    get_value_ptr возвращает ctypes-указатель на непрерывный буфер.
    Через ctypes можно прочитать значения, совпадающие с get_value.
    """
    import ctypes

    for name in bmi.get_var_names():
        ptr = bmi.get_value_ptr(name)
        buf = bmi._values[name]

        # Проверяем тип указателя в зависимости от dtype
        if buf.dtype == np.float64:
            expected_type = ctypes.POINTER(ctypes.c_double)
        elif buf.dtype == np.int64:
            expected_type = ctypes.POINTER(ctypes.c_int64)
        else:
            pytest.fail(f"{name}: неизвестный dtype {buf.dtype}")

        assert isinstance(ptr, expected_type), (
            f"{name}: ожидался {expected_type}, получено {type(ptr)}"
        )

        # Проверяем, что через указатель видны те же данные
        n = buf.size
        arr_from_ptr = np.ctypeslib.as_array(ptr, shape=(n,))
        assert np.array_equal(arr_from_ptr, buf), (
            f"{name}: данные через указатель не совпадают с буфером"
        )


@pytest.mark.integration
def test_get_value_at_indices(bmi):
    """get_value_at_indices возвращает корректные значения."""
    name = "soil_water_volumetric"
    indices = [0, 2, 4]
    vals = bmi.get_value_at_indices(name, indices)
    expected = bmi.get_value(name)[indices]
    assert np.allclose(vals, expected)


@pytest.mark.integration
def test_set_value_sends_to_apsim(bmi):
    """
    set_value('soil_psi', ...) откладывает запись до следующего paused1.
    Локальный буфер обновляется сразу, ошибок быть не должно.
    """
    name = "soil_psi"
    original = bmi.get_value(name).copy()
    new = original + 0.001

    try:
        bmi.set_value(name, new)
    except Exception as e:
        pytest.fail(f"set_value упал: {e}")

    # локальный буфер должен измениться сразу
    v = bmi.get_value(name)
    assert np.allclose(v, new)

    # восстанавливаем
    bmi.set_value(name, original)


@pytest.mark.integration
def test_set_value_wrong_shape_raises(bmi):
    """set_value с неправильной формой кидает ValueError."""
    with pytest.raises(ValueError):
        bmi.set_value("soil_psi", np.zeros(bmi._n_layers + 1))


@pytest.mark.integration
def test_set_value_unknown_var_raises(bmi):
    """set_value с неизвестной переменной кидает KeyError."""
    with pytest.raises(KeyError):
        bmi.set_value("unknown_variable", 0.0)


@pytest.mark.integration
def test_set_value_at_indices(bmi):
    """set_value_at_indices тоже откладывает запись, локальный буфер меняется сразу."""
    name = "soil_psi"
    original = bmi.get_value(name).copy()
    indices = [1, 3]
    new_vals = [original[1] + 10.0, original[3] + 10.0]

    try:
        bmi.set_value_at_indices(name, indices, new_vals)
    except Exception as e:
        pytest.fail(f"set_value_at_indices упал: {e}")

    updated = bmi.get_value(name)
    assert updated[1] == pytest.approx(new_vals[0])
    assert updated[3] == pytest.approx(new_vals[1])

    # восстанавливаем
    bmi.set_value(name, original)


# =========================================================
# 7. ТЕСТЫ ШАГА
# =========================================================
@pytest.mark.integration
def test_update_increments_time(bmi):
    """update() увеличивает current_time на time_step."""
    t0 = bmi.get_current_time()
    bmi.update()
    t1 = bmi.get_current_time()
    assert t1 == pytest.approx(t0 + bmi.get_time_step())


@pytest.mark.integration
def test_update_produces_valid_values(bmi):
    """После update() все значения валидны."""
    for name in bmi.get_var_names():
        v = bmi.get_value(name)
        assert not np.any(np.isnan(v))
        assert not np.any(np.isinf(v))


@pytest.mark.integration
def test_update_until_advances(bmi):
    """update_until(t) доводит current_time до t."""
    target = bmi.get_current_time() + 3.0
    bmi.update_until(target)
    assert bmi.get_current_time() == pytest.approx(target)


@pytest.mark.integration
def test_update_until_beyond_end_raises(bmi):
    """update_until за пределами end_time кидает ValueError."""
    with pytest.raises(ValueError):
        bmi.update_until(bmi.get_end_time() + 100.0)


# =========================================================
# 8. ТЕСТЫ ОШИБОК
# =========================================================
def test_get_value_unknown_var_raises():
    """get_value с неизвестной переменной кидает KeyError."""
    model = BmiApsimPinn()
    with pytest.raises(KeyError):
        model.get_value("unknown_variable")


def test_get_var_type_unknown_raises():
    """get_var_type с неизвестной переменной кидает KeyError."""
    model = BmiApsimPinn()
    with pytest.raises(KeyError):
        model.get_var_type("unknown_variable")


@pytest.mark.integration
def test_set_value_soil_psi_sends_to_apsim(bmi):
    """set_value('soil_psi', ...) не падает (отправка в APSIM через ZMQ)."""
    original = bmi.get_value("soil_psi").copy()
    try:
        bmi.set_value("soil_psi", original)  # те же значения
    except Exception as e:
        pytest.fail(f"set_value('soil_psi') упал: {e}")


# =========================================================
# 9. ТЕСТЫ СЕТКИ И ПЕРЕМЕННЫХ НА СОГЛАСОВАННОСТЬ
# =========================================================
@pytest.mark.integration
def test_all_array_vars_have_grid_size(bmi):
    """Все ARRAY_VARS имеют размер == grid_size."""
    for name in ARRAY_VARS:
        assert bmi.get_value(name).size == bmi.get_grid_size(0)


@pytest.mark.integration
def test_all_scalar_vars_have_size_1(bmi):
    """Все SCALAR_VARS имеют размер == 1."""
    for name in SCALAR_VARS:
        assert bmi.get_value(name).size == 1


@pytest.mark.integration
def test_input_and_output_vars_are_subset_of_all(bmi):
    """INPUT_VARS и OUTPUT_VARS — подмножества BMI_TO_APSIM.keys()."""
    all_vars = set(BMI_TO_APSIM.keys())
    assert set(INPUT_VARS).issubset(all_vars)
    assert set(OUTPUT_VARS).issubset(all_vars)


@pytest.mark.integration
def test_no_overlap_between_input_and_output(bmi):
    """INPUT_VARS и OUTPUT_VARS не пересекаются."""
    assert set(INPUT_VARS).isdisjoint(set(OUTPUT_VARS))