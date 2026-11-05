from pathlib import Path
import json
from docx import Document
from docx.shared import Inches,Pt

root=Path(__file__).resolve().parents[1];folder=root/'runs/analysis/project_report';charts=root/'for_me/night_analysis'
folder.mkdir(parents=True,exist_ok=True)
doc=Document(root/'for_me/project_report/Отчет_по_проекту_APSIM_AquaCrop_2026_11_02.docx')
items=json.loads((charts/'charts.json').read_text(encoding='utf-8'))
for i,(name,title,note) in enumerate(items):
    heading=doc.add_heading(title,level=1);heading.paragraph_format.page_break_before=True
    if i==0:
        doc.add_paragraph('Дополнение от 3 ноября 2026 года. Очередь завершилась: шесть поисков, 240 оценок кандидатов. Графики относятся к старому правилу запрета потерь Humic. Новый режим Humic только как цели к этим результатам не применялся. Основная часть документа выше сохраняет состояние проекта на 2 ноября.')
    image=doc.add_picture(str(charts/(name+'.png')),width=Inches(7))
    image._inline.docPr.set('descr',title+'. '+note)
    para=doc.add_paragraph('Рисунок '+str(i+3)+'. '+title,style='Caption')
    para.paragraph_format.space_after=Pt(9)
    doc.add_paragraph(note)
    if i==1:doc.add_paragraph('Прибыль чистого AquaCrop нельзя напрямую сравнивать с прибылью связки: в связке учитывается отдельный счёт за удобрения и их внесение. Одинаковое название «прибыль» здесь не означает одинаковый состав расходов.')
    if i==3:doc.add_paragraph('Во всех шести поисках выбран начальный candidate_001. Это не доказывает оптимальность начальной карты: другие проверенные карты не прошли ограничения, а объём поиска ограничен.')
    if i==5:doc.add_paragraph('Численная корректность расчёта и точность урожая — разные проверки. Для будущих лет нет наблюдений для MAE/RMSE; профили AquaCrop некалиброванные. Низкая невязка воды не устраняет эту неопределённость.')
output=folder/'Отчет_APSIM_AquaCrop_с_графиками_2026_11_03.docx';doc.save(output);print(output)
