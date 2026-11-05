"""Build a source-backed Russian Word report of the completed project work."""
from pathlib import Path
import csv
import json
from docx import Document
from docx.shared import Inches,Pt,RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT,WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/analysis/project_report';QA=OUT/'qa'
OUT.mkdir(parents=True,exist_ok=True);QA.mkdir(exist_ok=True)
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
metrics=read(ROOT/'for_me/apsim_new_calibr/metrics.json')
aq_analysis=read(ROOT/'for_me/aquacrop_future_analysis/analysis.json');aqrun=Path(aq_analysis['source_run'])
aq=read(aqrun/'evaluations.json');base=read(aqrun/'baseline_score.json')
native_run=ROOT/'runs/apsim/native_multiyear/optimize_20261029_123240_766585'
native=read(native_run/'evaluations.json');nativebase=read(native_run/'baseline_score.json')
pure_run=ROOT/'runs/aquacrop_standalone/yield+profit+water_use+drainage_20261102_152655_764066'
pure=read(pure_run/'optimization_summary.json');pure_selected=read(pure_run/'selected_result.json')
night=Path(read(ROOT/'for_me/night_optimization/latest.json')['batch']);queue=read(night/'status.json')
ar={r['candidate_id']:r for r in aq};nr={r['candidate_id']:r for r in native}


def n(value,digits=2):return f'{value:,.{digits}f}'.replace(',',' ').replace('.',',')


def font(size,bold=False):
    return ImageFont.truetype('C:/Windows/Fonts/'+('arialbd.ttf' if bold else 'arial.ttf'),size)


def figures():
    im=Image.new('RGB',(1600,900),'white');d=ImageDraw.Draw(im)
    left,right,top,bottom=150,1510,130,745
    px=lambda x:left+(x-185)/(255-185)*(right-left)
    py=lambda y:bottom-(y+550)/750*(bottom-top)
    d.text((65,22),'Урожай и финансовый результат',fill='black',font=font(41,True))
    d.text((65,82),'Финансовый результат за пять лет   тыс условных рублей',fill='black',font=font(29))
    for y in (-500,-250,0,200):
        yy=py(y);d.line((left,yy,right,yy),fill='#D9D9D9',width=2)
        d.text((left-120,yy-20),str(y),fill='black',font=font(31))
    for x in (190,210,230,250):
        xx=px(x);d.text((xx-30,bottom+12),str(x),fill='black',font=font(31))
    d.line((left,top,left,bottom,right,bottom),fill='black',width=3)
    for row in aq:
        x,y=px(row['yield_dm_kg']/1000),py(row['net_profit_proxy']/1000)
        d.ellipse((x-10,y-10,x+10,y+10),fill='#7795A5')
    for key,label,dx,dy in [('candidate_001','001',14,5),('candidate_004','004',15,-35),('candidate_008','008',-90,-32)]:
        r=ar[key];x,y=px(r['yield_dm_kg']/1000),py(r['net_profit_proxy']/1000)
        d.ellipse((x-13,y-13,x+13,y+13),fill='#C58340',outline='black',width=2)
        d.text((x+dx,y+dy),label,fill='black',font=font(31,True))
    x,y=px(base['yield_dm_kg']/1000),py(base['net_profit_proxy']/1000)
    d.rectangle((x-11,y-11,x+11,y+11),fill='#555555');d.text((x+16,y-17),'База',fill='black',font=font(31))
    d.text((380,810),'Урожай за 2025–2029 годы   т сухой массы',fill='black',font=font(34))
    im.save(QA/'objectives.png')
    im=Image.new('RGB',(1600,620),'white');d=ImageDraw.Draw(im)
    d.text((65,18),'Изменение Humic у candidate_001',fill='black',font=font(40,True))
    d.text((65,75),'Килограммы углерода на гектар   конец 2029 года к концу 2024 года',fill='black',font=font(28))
    left,right,top,bottom=150,1510,145,500
    yy=lambda v:bottom-(v+150)/600*(bottom-top)
    for value in (-150,0,200,400):
        y=yy(value);d.line((left,y,right,y),fill='#D9D9D9',width=2);d.text((left-115,y-17),str(value),fill='black',font=font(29))
    stocks=ar['candidate_001']['humic']
    for i in range(1,11):
        stock=stocks['Field'+str(i)];v=stock['end_kg_ha']-stock['start_kg_ha'];x=left+(i-.5)*(right-left)/10
        y0,y1=sorted([yy(0),yy(v)]);d.rectangle((x-35,y0,x+35,y1),fill='#B4574B' if v<0 else '#587F91')
        d.text((x-15,bottom+15),str(i),fill='black',font=font(32))
    d.line((left,yy(0),right,yy(0)),fill='black',width=2)
    d.text((660,565),'Номер поля',fill='black',font=font(31));im.save(QA/'humic.png')


doc=Document();sec=doc.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=Inches(.7);sec.bottom_margin=Inches(.7);sec.left_margin=Inches(.75);sec.right_margin=Inches(.75)
for name in ('Normal','Title','Subtitle','Heading 1','Heading 2','Caption'):
    style=doc.styles[name];style.font.name='Arial';style.font.color.rgb=RGBColor(0,0,0)
    style._element.get_or_add_rPr().rFonts.set(qn('w:hAnsi'),'Arial')
normal=doc.styles['Normal'];normal.font.size=Pt(11)
normal.paragraph_format.line_spacing=1.1;normal.paragraph_format.space_after=Pt(7)
for name,size in [('Title',23),('Heading 1',16),('Heading 2',12)]:
    st=doc.styles[name];st.font.size=Pt(size);st.paragraph_format.space_after=Pt(10);st.paragraph_format.keep_with_next=True
doc.styles['Heading 1'].paragraph_format.space_before=Pt(0)
doc.styles['Heading 2'].paragraph_format.space_before=Pt(10)
doc.styles['Caption'].font.size=Pt(9.5);doc.styles['Caption'].font.italic=False
doc.core_properties.title='Моделирование и оптимизация посевов в APSIM и AquaCrop'
doc.core_properties.subject='Отчёт о выполненных работах и проверенных результатах на 2 ноября 2026 года'
doc.core_properties.author='Проект APSIM и AquaCrop'
foot=sec.footer.paragraphs[0];foot.alignment=WD_ALIGN_PARAGRAPH.RIGHT
foot.add_run('Страница ').font.size=Pt(9)
fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');foot._p.append(fld)


def p(text,bold=False):
    para=doc.add_paragraph();para.paragraph_format.widow_control=True
    run=para.add_run(text);run.bold=bold;return para


def heading(text):doc.add_heading(text,level=2)


def page(title):
    paragraph=doc.add_heading(title,level=1)
    paragraph.paragraph_format.page_break_before=True


def table(headers,rows,widths):
    t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
    for cell,width in zip(t.columns,widths):cell.width=Inches(width)
    pr=t._tbl.tblPr;borders=OxmlElement('w:tblBorders')
    for edge in ('top','left','bottom','right','insideH','insideV'):
        el=OxmlElement('w:'+edge);el.set(qn('w:val'),'single');el.set(qn('w:sz'),'5');el.set(qn('w:color'),'D9D9D9');borders.append(el)
    pr.append(borders)
    for i,text in enumerate(headers):t.rows[0].cells[i].text=str(text)
    for row in rows:
        cells=t.add_row().cells
        for cell,text in zip(cells,row):cell.text=str(text)
    for ri,row in enumerate(t.rows):
        cant=OxmlElement('w:cantSplit');row._tr.get_or_add_trPr().append(cant)
        if ri==0:
            repeat=OxmlElement('w:tblHeader');row._tr.get_or_add_trPr().append(repeat)
        for ci,cell in enumerate(row.cells):
            cell.width=Inches(widths[ci]);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props=cell._tc.get_or_add_tcPr();shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'284B63' if ri==0 else ('F1F5F7' if ri%2==0 else 'FFFFFF'));props.append(shade)
            margins=OxmlElement('w:tcMar')
            for side,val in [('top','80'),('bottom','80'),('left','95'),('right','95')]:
                node=OxmlElement('w:'+side);node.set(qn('w:w'),val);node.set(qn('w:type'),'dxa');margins.append(node)
            props.append(margins)
            for para in cell.paragraphs:
                para.paragraph_format.space_after=Pt(2);para.paragraph_format.space_before=Pt(2);para.paragraph_format.line_spacing=1.05
                if ci>0:para.alignment=WD_ALIGN_PARAGRAPH.CENTER
                for run in para.runs:run.font.size=Pt(10);run.bold=ri==0;run.font.color.rgb=RGBColor(255,255,255) if ri==0 else RGBColor(0,0,0)
    doc.add_paragraph().paragraph_format.space_after=Pt(0)


def code(text):
    para=doc.add_paragraph();para.paragraph_format.space_after=Pt(6)
    run=para.add_run(text);run.font.name='Consolas';run.font.size=Pt(9.5)


def equation(text):
    para=doc.add_paragraph();math=OxmlElement('m:oMath');run=OxmlElement('m:r')
    props=OxmlElement('m:rPr');normal_text=OxmlElement('m:nor');normal_text.set(qn('m:val'),'1');props.append(normal_text);run.append(props)
    wprops=OxmlElement('w:rPr');fonts=OxmlElement('w:rFonts');fonts.set(qn('w:ascii'),'Arial');fonts.set(qn('w:hAnsi'),'Arial');wprops.append(fonts);run.append(wprops)
    val=OxmlElement('m:t');val.text=text;run.append(val);math.append(run);para._p.append(math)


figures()
doc.add_paragraph('Моделирование и оптимизация посевов в APSIM и AquaCrop',style='Title')
p('Отчёт о выполненных работах',True)
p('Проект apsim_and_aquacrop_optimization\nСостояние на 2 ноября 2026 года')
p('В проекте подготовлены три сценария расчёта будущих карт посевов, реализован выбор целевых функций и подключены NSGA-II и NSGA-III. Настроена последовательная ночная очередь из шести поисков. Проведены полные расчёты исторической модели, завершён поиск связки AquaCrop с APSIM и проверен самостоятельный AquaCrop.')
p('Главный результат: вычислительные сценарии работают, но завершённые поиски с ограничением сохранности Humic пока не дали допустимого решения. Допустимый вариант самостоятельного AquaCrop относится к иной задаче, в которой Humic и минеральный азот не рассчитываются.',True)
heading('Три расчётных сценария')
table(['Сценарий','Урожай','Почвенные показатели'],[
    ['APSIM','Нативные уборки APSIM','Humic и минеральный N из APSIM'],
    ['AquaCrop с APSIM','Нативные уборки AquaCrop','Humic и N из APSIM на той же карте'],
    ['Самостоятельный AquaCrop','Нативные уборки AquaCrop','Собственный водный баланс без Humic и N']], [2.15,2.15,2.7])
heading('Что подтверждено расчётами')
p('Историческая модель APSIM: 2004–2023 годы, десять полей, 200 записей об уборках. Историческая оптимизация: 40 успешно рассчитанных вариантов, допустимых нет. Будущий поиск связки: 40 успешных вариантов, допустимых нет. Самостоятельный AquaCrop: короткий поиск из восьми вариантов, один допустимый по структуре посевов.')
p('Ночная очередь настроена. Её фактическое состояние приведено в разделе о последовательном запуске. Отчёт разделяет результаты расчётов, программные возможности и ограничения модельных допущений.')

page('Обновление исторической модели APSIM')
p('Новая симуляция и погодный файл перенесены в models/apsim/simulation/calibrated_history. Экспорт модели сохранён вместе с параметрами, метриками и сведениями о происхождении. Старые сценарии оставлены отдельно. Запуск python -m agro --config launchers/configs/apsim_baseline.json --simulate-only выполняет нативную симуляцию, а не оптимизацию. [S1]')
heading('Период и устройство модели')
p('Часы модели охватывают 1 января 2004 года — 31 декабря 2023 года. Каждый из десяти участков имеет площадь 1 га. Менеджер RotationManager задаёт культуры, сорта, густоту, глубину, междурядье, сроки посева и уборки, использование урожая и возраст травостоя. Удобрения задаются отдельным менеджером Fertilise at sowing.')
table(['Показатель','Проверенный результат'],[
    ['Период модели','2004–2023'],['Количество полей','10 по 1 га'],['Суточных строк на поле',str(metrics['fields'][0]['days'])],
    ['Записей об уборках',str(metrics['harvest_records'])],['Ошибок APSIM',str(metrics['errors'])],['Предупреждений',str(metrics['warning_count'])],
    ['Суммарная сухая масса за 20 лет',n(metrics['production_t_dm'])+' т'],
    ['Различие с воспроизведением калибровки',n(metrics['max_replay_vs_calibration_difference_t_ha'],3)+' т/га']], [4.2,2.8])
heading('Проверка соответствия наблюдениям')
p(f'Для {metrics["fit"]["n"]} сопоставимых наблюдений MAE составляет {n(metrics["fit"]["mae_t_ha"],3)} т/га, RMSE — {n(metrics["fit"]["rmse_t_ha"],3)} т/га, объединённый R² — {n(metrics["fit"]["r2"],3)}. Отрицательный R² показывает, что точное воспроизведение экспорта не означает высокой точности относительно всех наблюдений. Сравнение с прежними метриками на 130 наблюдениях не является парным сравнением одной выборки.')
p('В фенологии сопоставлены 305 дат; 302 события достигнуты. Средняя абсолютная ошибка по достигнутым событиям составляет около 26,4 суток. Для ячменя отмечены нулевые записи зернового урожая при наличии надземной биомассы; причина требует отдельной проверки. Предупреждения об исходном состоянии и восстановлении травостоя сохранены в исходных отчётах.')

page('Историческая оптимизация управления APSIM')
p('Многолетняя оптимизация адаптирована к новому RotationManager. Полная история 2004–2023 годов рассчитывается для каждого кандидата, а целевые функции оцениваются только за 2020–2023 годы. Историческая последовательность культур остаётся фиксированной. Поиск изменяет технологию возделывания. [S2]')
p('Отдельно сохранено раннее гибридное исследование 36 режимов на прежней модели: нативный урожай AquaCrop корректировался разностью урожая кандидата и референса APSIM. Это внешний перенос отклика удобрений и глубины. Новый будущий поиск связки использует нативный урожай AquaCrop без этой поправки. [S8]')
table(['Группа культур','Изменяемые параметры','Число'],[
    ['Яровая пшеница','Сорт, густота, глубина, междурядье, схема удобрения','5'],
    ['Овёс с подсевом','Густота, глубина, схема удобрения','3'],
    ['Аналог озимой ржи','Густота и схема удобрения','2']], [1.65,4.5,.85])
p('Всего десять целочисленных переменных. Схема удобрения объединяет дозу и форму азота. Разрешены дозы от 0 до 210 кг N/га с шагом 30; при ненулевой дозе используются четыре заданные формы. Политики общие для соответствующей культуры на всех полях и годах оценки.')
heading('Полученные диагностические лидеры')
table(['Вариант','Урожай т СМ','Результат тыс усл руб','Полей с потерей Humic'],[
    ['Базовое управление',n(nativebase['yield_dm_kg']/1000),n(nativebase['net_profit_proxy']/1000),'8'],
    *[[key,n(nr[key]['yield_dm_kg']/1000),n(nr[key]['net_profit_proxy']/1000),str(sum(v>0 for v in nr[key]['G']))] for key in ['candidate_028','candidate_014','candidate_018','candidate_032']]], [2.3,1.2,1.7,1.8])
p('028 — максимум урожая; 014 — лучший финансовый результат; 018 — минимум дефицита N; 032 — минимум суммы положительных нарушений Humic. Все перечисленные кандидаты недопустимы.')
p('NSGA-II использовал три цели: максимум сухой массы, максимум расчётного финансового результата и минимум дефицита минерального N относительно базы. Сохранность Humic проверялась отдельно по каждому полю между 31 декабря 2019 года и 31 декабря 2023 года.')
p('Все 40 кандидатов рассчитаны успешно, но ни один не прошёл ограничения Humic. Официальный фронт Парето пуст. Лидеры перенесены в будущее планирование только как фиксированные схемы управления. Их исторические значения урожая и экономики не подставляются вместо будущих симуляций.')

page('Построение будущих карт посевов')
p('Восстановлен планировщик plan_crop_rotation.py с модулями rotation_planner и rotation_visualization. Он строит последовательности культур из исходных состояний полей и правил переходов. Самостоятельный расчёт совместимой карты по матрице не оценивает урожай, прибыль или состояние почвы. [S3]')
heading('Пример на 2025–2029 годы')
p('Горизонт оставлен по согласованному примеру 2025–2029, исходные состояния полей заданы за 2024 год в конфигурации. Учитываются матрица предшественников, обязательное продолжение травостоя и возраст многолетних трав. В исходной матричной версии количества полей под культурами служили для выбора между вариантами; в симуляционной оптимизации эти диапазоны стали жёсткими ограничениями.')
p('Для новой модели доступны яровая пшеница, аналог озимой ржи, овёс с подсевом и травостой первого, второго и третьего года. Картофель и отдельная бобово-злаковая смесь исключены до подготовки соответствующих расчётных профилей.')
table(['Этап','Вход','Выход'],[
    ['Допустимые последовательности','Состояния 2024 года и матрица','Набор пятилетних вариантов для поля'],
    ['Кандидат оптимизатора','Десять индексов последовательностей','Одна карта для десяти полей'],
    ['Расчёт движком','Карта, почва, погода и управление','Уборки и доступные показатели'],
    ['Оценка','Результаты симуляции и ограничения','Вектор целей F и нарушений G']], [1.9,2.65,2.45])
heading('Погода и начальные состояния')
p('Исходная погода заканчивается одиночной записью 1 января 2024 года. В будущих сценариях используются явные исторические аналоги: 2024–2029 соответствуют 2018–2023 годам. Даты сопоставляются по месяцу и дню; недостающий 29 февраля интерполируется. Это сценарный пример, а не прогноз погоды.')
p('Для APSIM история продолжается непрерывно с 2004 года. В связке AquaCrop начинается с мостового 2024 года и получает послойную влажность из APSIM на конец 2023 года. В самостоятельном AquaCrop начальная влажность задаётся собственной предпосылкой полевой влагоёмкости.')

page('Целевые функции и алгоритмы поиска')
p('Общая функция search_integer получает списки допустимых вариантов, функцию evaluate, настройки алгоритма, количество целей и ограничений. Кандидат представляет собой целочисленный вектор индексов. evaluate запускает расчёт модели и возвращает F и G. Значение G не больше нуля означает выполнение соответствующего ограничения. [S4]')
table(['Код цели','Направление','Где используется'],[
    ['yield','Максимум сухой массы','Все три модели'],['profit','Максимум расчётного результата','Все три модели'],
    ['mineral_n','Минимум дефицита N к базе','APSIM и связка'],['humic','Лучшее относительное изменение на худшем поле','APSIM и связка'],
    ['water_use','Минимум эвапотранспирации','Самостоятельный AquaCrop'],
    ['water_productivity','Максимум кг СМ на м³ эвапотранспирации','Самостоятельный AquaCrop'],
    ['drainage и runoff','Минимум дренажа и стока','Самостоятельный AquaCrop']], [1.65,2.9,2.45])
p('Все компоненты F минимизируются. Поэтому урожай Y и финансовый результат P передаются со знаком минус. Для трёх исторических целей вектор имеет следующий вид; D обозначает нормированный дефицит N.')
equation('F = (−Y, −P, D)')
p('Автоматический выбор: 1–3 критерия — NSGA-II, 4 и более — NSGA-III. Для NSGA-III строятся опорные направления Das–Dennis; размер популяции должен быть не меньше их числа. При четырёх целях и популяции 10 используются два разбиения и десять направлений.')
p('Отбор, скрещивание и мутация создают новые комбинации индексов. Используются SBX с вероятностью 0,9 и параметром 15, полиномиальная мутация с параметром 20 и округление до целых индексов. Повторные карты кэшируются. Число решений Парето зависит от допустимости и взаимных компромиссов целей.')
p('В консоли n_gen — поколение, n_eval — число оценок, cv_min — минимальная сумма нормированных нарушений. Нулевой cv_min означает наличие допустимого варианта. Положительное значение нельзя непосредственно читать как процент потери Humic. Для показа одной карты из Парето применяется явно заданное правило нормированного расстояния до идеальной точки; остальные варианты сохраняются.')

page('Результаты связки AquaCrop и APSIM')
p('Нативный урожай AquaCrop подключён к новой карте посевов. Humic, минеральный азот и фактические внесения удобрений рассчитывает APSIM на той же карте и погоде. Аддитивная поправка урожая из старого гибридного исследования здесь не применяется. Обратной связи биомассы AquaCrop в почвенный баланс APSIM нет. [S5]')
p(f'Завершён поиск за 2025–2029 годы с целями yield и profit. Рассчитаны {len(aq)} кандидатов за {n(aq_analysis["elapsed_minutes"],1)} минуты. Все расчёты технически успешны; допустимых решений нет. Только candidate_001 удовлетворяет структуре посевов.')
table(['Вариант','Урожай т СМ','Результат тыс усл руб','Полей с потерей Humic'],[
    ['Исходная база',n(base['yield_dm_kg']/1000),n(base['net_profit_proxy']/1000),'8'],
    *[[key,n(ar[key]['yield_dm_kg']/1000),n(ar[key]['net_profit_proxy']/1000),str(sum(v>0 for v in ar[key]['humic_constraints_G']))] for key in ['candidate_001','candidate_008','candidate_004']]], [2.3,1.2,1.7,1.8])
p('Все значения в таблице — суммарно по десяти полям за пять лет. candidate_008 максимизирует урожай, candidate_004 даёт наибольший расчётный финансовый результат. Оба нарушают структуру посевов и ограничения Humic. Их нельзя считать готовыми допустимыми планами.')
heading('Причины выбора начальной карты')
p('candidate_001 совпадает с начальной картой и имеет наименьшую сумму нарушений. Humic на поле 3 уменьшается на 114,85 кг C/га, или 0,338%; на поле 6 — на 67,37 кг C/га, или 0,198%. На этих двух полях снижение отмечено у всех сорока кандидатов. 39 карт дополнительно нарушили диапазоны структуры посевов.')
heading('Почему ухудшилась экономика')
p('На одной и той же карте переход к выбранной схеме управления увеличил урожай лишь на 0,27%. Затраты на удобрения выросли с 407,65 до 739,96 тыс. условных рублей, а финансовый результат ухудшился примерно на 324,15 тыс. рублей. Внесение N увеличилось с 1260 до 4800 кг за горизонт.')
p('Прямой отклик урожая AquaCrop на дозу N в этой связке не введён. Доза влияет на расчёт APSIM и затраты, но не преобразуется автоматически в стресс плодородия AquaCrop. Выручка задана только для Wheat и Oats, корма имеют нулевую цену. Экономика является диагностической.')

page('Графическое сравнение результатов связки')
doc.add_picture(str(QA/'objectives.png'),width=Inches(7))
doc.add_paragraph('Рисунок 1. Уборки AquaCrop и расчётный результат за пять лет. Точки 001, 004 и 008 выделены для диагностики; все кандидаты нарушают хотя бы одно ограничение. [S5]',style='Caption')
doc.add_picture(str(QA/'humic.png'),width=Inches(7))
doc.add_paragraph('Рисунок 2. Изменение Humic у выбранного диагностического варианта. Положительное изменение на другом поле не компенсирует потери на полях 3 и 6. Единица — кг C/га, а не процент лабораторного гумуса. [S5]',style='Caption')

page('Самостоятельная модель AquaCrop')
p('Создан отдельный набор входов models/aquacrop_standalone. Он содержит собственные JSON почвы, начальной влажности, календарей, профилей культур и последовательностей посевов, локальную погоду, матрицу переходов и нативные шаблоны AquaCrop. Во время расчёта модель не открывает APSIM, его результаты или схемы управления из прошлой оптимизации. [S6]')
p('Постоянные почвы и календарные профили скопированы однократно с записью происхождения. Начальная влажность каждого слоя на 1 января 2024 года равна полевой влагоёмкости. Мостовой 2024 год рассчитывается до оценки периода 2025–2029. Это собственное начальное условие, а не импорт рассчитанного состояния APSIM.')
table(['Цель','Нативная величина и единицы'],[
    ['yield','Суммарная убранная сухая масса, кг'],['profit','Выручка минус заданные затраты, условные рубли'],
    ['water_use','Испарение и транспирация, м³'],['water_productivity','Сухая масса на эвапотранспирацию, кг/м³'],
    ['drainage','Дренаж, м³'],['runoff','Поверхностный сток, м³']], [2,5])
p('Один миллиметр воды на гектаре соответствует 10 м³. water_use описывает эвапотранспирацию богарного сценария и не означает подачу поливной воды. Humic и минеральный N недоступны и не заменяются нулевыми значениями. Ограничения касаются структуры посевов; ошибочный водный баланс приводит к отклонению расчёта.')
heading('Проверка прохождения через оптимизатор')
p(f'Проведён короткий реальный поиск NSGA-III: популяция {pure["search_settings"]["population"]}, {pure["search_settings"]["generations"]} поколения, четыре цели. Все {pure["successful"]} кандидатов рассчитаны успешно; {pure["feasible"]} вариант допустим по структуре посевов. Проверена работа при заведомо недоступном APSIM. Это проверка контура, а не полный штатный поиск и не подтверждение сходимости.')
p('Экономика задаётся самостоятельно: 14,3 условного рубля за кг сухой массы Wheat/Oats, цена Mix по умолчанию нулевая; совокупные затраты — 23 000 руб./га/год для каждого состояния культуры. Счета за удобрения из APSIM не импортируются. Поэтому прибыль напрямую несопоставима с прибылью связки.')

page('Последовательный ночной запуск')
p('Разработана очередь из шести полных оптимизаций. Для каждого этапа создаются отдельная папка, журнал и зафиксированная копия конфигурации с абсолютными путями. Новые симуляции запускаются только после завершения предыдущего этапа. Между этапами не требуется нажимать клавишу. [S7]')
table(['№','Модель','Алгоритм','Цели'],[
    ['1','APSIM','NSGA-II','Урожай и экономика'],['2','APSIM','NSGA-III','Урожай, экономика, N, Humic'],
    ['3','AquaCrop с APSIM','NSGA-II','Урожай и экономика'],['4','AquaCrop с APSIM','NSGA-III','Урожай, экономика, N, Humic'],
    ['5','Чистый AquaCrop','NSGA-II','Урожай и экономика'],['6','Чистый AquaCrop','NSGA-III','Урожай, экономика, ET, дренаж']], [.65,1.8,1.25,3.3])
p('Штатный бюджет каждого поиска — 10 особей и четыре поколения, seed 42: ориентировочно до 40 оценок на этап. Повторы могут уменьшить число уникальных симуляций. Для APSIM и связки отдельно рассчитывается базовый сценарий. Прежняя скорость даёт ориентир 6–9 часов на очередь; фактическое время не гарантировано.')
heading('Ошибки остановка и возобновление')
p('Техническая ошибка записывается в журнал, после чего очередь по умолчанию переходит к следующему этапу. Статус no_feasible_solution означает завершённый поиск без допустимого решения и не считается аварией. Ctrl+C останавливает собственное дерево процессов текущего этапа.')
p('Повторный запуск с --resume пропускает завершённые этапы. Прерванный или ошибочный этап начинает новую попытку в отдельной папке; возобновление с промежуточного поколения не реализовано. Активный дочерний процесс предыдущей попытки блокирует параллельный повтор. Операционная блокировка предотвращает одновременный запуск двух очередей.')
p('На время очереди включается временное предотвращение автоматического сна Windows. Завершение снимает запрос; системные настройки питания не изменяются. Явное выключение, сон или закрытие крышки этим не отменяются.')
pending=sum(j['stage']=='pending' for j in queue['jobs'])
queue_label={'prepared':'подготовлена','running':'выполняется','completed':'завершена','completed_with_errors':'завершена с ошибками','interrupted':'прервана'}.get(queue['stage'],queue['stage'])
p(f'На момент подготовки отчёта очередь {queue_label}; ожидающих этапов — {pending} из {len(queue["jobs"])}. Состояние взято из сохранённого status.json. Результаты шести ночных поисков в этот отчёт не включаются до их фактического завершения.',True)

page('Проверки ограничения и дальнейшая работа')
heading('Проверка программного контура')
p('После создания самостоятельного AquaCrop прошёл набор из 101 теста; после добавления очереди — 110 тестов. Проверялись размерности и знаки целей, целочисленные границы, воспроизводимость NSGA-II и NSGA-III, кэширование карт, перенос почвенной влажности в связке и отсутствие внешних почвенных показателей в самостоятельной модели.')
p('Для очереди проверены последовательность этапов, продолжение после технической ошибки, сохранение старых попыток, пропуск завершённых поисков, блокировка параллельного запуска и остановка собственного дерева процессов Windows. Подготовленные конфигурации всех шести этапов загружаются; подготовка сама по себе не запускает движки.')
heading('Границы интерпретации')
p('Калибровка APSIM не переносится автоматически в AquaCrop. Эффективные смеси не воспроизводят точную конкуренцию видов, полную зимостойкость, зрелые корни и предшествующие запасы многолетников. Для будущих лет используются исторические погодные аналоги и повторно применённые календарные профили.')
p('Humic — модельный пул углерода в кг C/га. Его нельзя непосредственно читать как лабораторный процент гумуса. Ограничение требует сохранения запаса отдельно на каждом поле. Положительная сумма изменения по хозяйству не делает вариант допустимым при потере на отдельном поле.')
p('Цели и ограничения трёх моделей различаются. У самостоятельного AquaCrop нет запрета на снижение Humic; экономические расходы заданы по другой схеме. Разные наборы целей NSGA-II и NSGA-III также не образуют чистого эксперимента по сравнению алгоритмов.')
heading('Приоритеты следующего этапа')
p('1. Повысить долю карт, удовлетворяющих структуре посевов, через генерацию или восстановление допустимых хозяйственных комбинаций. В завершённом поиске связки этому условию соответствовала только одна карта из сорока.')
p('2. Для полей 3 и 6 отдельно проверить способы изменения углеродного баланса: остатки, органические поступления и травостой. Дозы и эффекты должны быть явно представлены и проверены в модели; произвольное увеличение начального Humic не обосновано.')
p('3. Уточнить цены кормов и затраты, калибровать самостоятельные профили AquaCrop и определить способ представления плодородия перед совместной оптимизацией доз удобрений. Затем увеличивать бюджет поиска и анализировать устойчивость к погоде и seed.')

page('Команды запуска и источники результатов')
p('Команды выполняются из корня проекта. Без --run отдельные новые запускатели готовят входы; ночная очередь без --prepare-only запускает расчёты.')
heading('Основные команды')
code('python -m agro --config launchers/configs/apsim_baseline.json --simulate-only')
code('python -m agro.apsim.workflows.run_multiyear_rotation_optimizer --run --config models/apsim/configs/multiyear_rotation_native_2020_2023.json')
code('python run.py apsim --objectives yield profit --run')
code('python run.py coupled --objectives yield profit --run')
code('python run.py aquacrop --objectives yield profit --run')
code('python -m agro.night_queue --resume')
p('Для проверки списка ночных команд без расчётов используется --dry-run; для подготовки новой очереди — --prepare-only. Общий обзор результатов находится в for_me/night_optimization/index.html.')
heading('Источники внутри проекта')
sources=[
 ('S1','Историческая модель и её анализ','for_me/apsim_new_calibr/metrics.json'),
 ('S2','Исторический поиск управления','runs/apsim/native_multiyear/optimize_20261029_123240_766585/optimization_summary.json'),
 ('S3','Правила и будущий план','docs/future_rotation_planning.md; docs/future_rotation_objectives.md'),
 ('S4','Алгоритмы и оценка целей','agro/optimization/nsga2.py; agro/planning/future_scenarios.py'),
 ('S5','Завершённый поиск связки и анализ','for_me/aquacrop_future_analysis/analysis.json; index.html'),
 ('S6','Самостоятельный AquaCrop','docs/aquacrop_standalone.md; models/aquacrop_standalone/optimization.json'),
 ('S7','Ночная очередь','docs/night_optimization.md; launchers/configs/night_optimization.json'),
 ('S8','Раннее исследование внешнего отклика','docs/aquacrop_management_response.md; for_me/optimization/index.html')]
for tag,title,path in sources:
    para=doc.add_paragraph();para.paragraph_format.space_after=Pt(5)
    para.add_run(f'[{tag}] {title}. ').bold=True
    run=para.add_run(path.replace('/','/\u200b'));run.font.size=Pt(9.5)
p('Полные таблицы, графики, исходные симуляции и журналы сохраняются в проекте. В численных таблицах этого документа значения округлены; расчётные JSON и CSV содержат исходную точность.')
output=OUT/'Отчет_по_проекту_APSIM_AquaCrop_2026_11_02.docx'
for shape,description in zip(doc.inline_shapes,[
    'Сравнение суммарного урожая и финансового результата сорока недопустимых кандидатов AquaCrop с APSIM и исходной базы',
    'Изменение модельного углерода Humic на десяти полях у candidate_001 с потерями на полях 3 и 6']):
    shape._inline.docPr.set('descr',description)
for element in (doc._element,doc.styles._element):
    for border in list(element.xpath('.//w:pBdr')):border.getparent().remove(border)
doc.save(output)
print(output)
