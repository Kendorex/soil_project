import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.bmi_apsim_pinn import BmiApsimPinn
from pymt.framework.bmi_bridge import bmi_factory

MyModel = bmi_factory(BmiApsimPinn)
model = MyModel()

cfg_file = ROOT / "config" / "bmi_config.json"
model._initdir = str(cfg_file.parent) 
model.bmi.initialize(str(cfg_file))

print(f"Name: {model.get_component_name()}")
print(f"Start: {model.start_time}")
print(f"End:   {model.end_time}")
print(f"Step:  {model.time_step} {model.time_units}")

while model.time < model.end_time:
    model.update()
    psi = model.get_value("soil_psi")
    sw  = model.get_value("soil_water_volumetric")
    print(f"t={model.time}: psi[0:3]={psi[:3]}, sw[0:3]={sw[:3]}")

model.bmi.finalize()
print("Готово.")