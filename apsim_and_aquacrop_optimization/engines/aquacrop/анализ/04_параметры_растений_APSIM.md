# Явные параметры растений в APSIM

Извлечены все непустые команды `Models.PMF.Cultivar` текущих десяти полей. Одинаковые команды объединены с перечнем полей. Это **явные переопределения**, а не полный набор параметров растений APSIM: базовые параметры могут загружаться из `ResourceName` установленной сборки APSIM. Их нельзя объявлять отсутствующими только потому, что их нет в .apsimx.

X/Y — узлы функции; FixedValue — постоянное значение. Единицы и ось функции необходимо сверять с ресурсом соответствующего вида APSIM, особенно если в имени пути они не указаны. Ниже значения сохранены дословно.


## Argamak

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].MinimumLeafNumber.FixedValue = 8 | Field3 | Минимальное число листьев в модели фенологии APSIM. |
| [Phenology].VrnSensitivity.FixedValue = 0 | Field3 | Чувствительность фенологии к яровизации; в .CRO отдельного аналога нет. |
| [Phenology].PpSensitivity.FixedValue = 0.3 | Field3 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Structure].Phyllochron.BasePhyllochron.FixedValue = 50 | Field3 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].ExtinctionCoeff.FixedValue = 0.5 | Field3 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Leaf].CohortParameters.MaxArea.AreaLargestLeaves.FixedValue = 5000 | Field3 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].InitialLeaves[1].Area = 150 | Field3 | Площадь начального листа в когорте; для CC0 нужна общая площадь и густота. |
| [Leaf].CohortParameters.MaxArea.AgeFactor.XYPairs.X = 3,3.5,4.0,4.5,4.9 | Field3 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].CohortParameters.MaxArea.AgeFactor.XYPairs.Y = 0.1,0.1,0.4,1,0.55 | Field3 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Structure].HeightModel.PotentialHeight.XYPairs.Y = 10,150,550,750 | Field3 | Функция потенциальной высоты; отдельной траектории высоты в текущем .CRO нет. |
| [Structure].BranchingRate.PotentialBranchingRate.Vegetative.PotentialBranchingRate.XYPairs.X = 1,2,3,4,5,6,7,8 | Field3 | Потенциальная скорость ветвления по фазам/состоянию; прямого аналога нет. |
| [Structure].BranchingRate.PotentialBranchingRate.Vegetative.PotentialBranchingRate.XYPairs.Y = 0,1,1,1,0.5,0,0,0 | Field3 | Потенциальная скорость ветвления по фазам/состоянию; прямого аналога нет. |
| [Oats].Structure.BranchingRate.StressFactors.CoverEffect.XYPairs.X = 1,1,1 | Field3 | Влияние покрытия на ветвление; не самостоятельный параметр .CRO. |
| [Oats].Leaf.CohortParameters.SpecificLeafAreaMax.XYPairs.X = 3,4.3,4.5,4.8 | Field3 | Максимальная удельная листовая площадь: площадь на массу; X/Y задают функцию по стадии модели. |
| [Oats].Leaf.CohortParameters.SpecificLeafAreaMax.XYPairs.Y = 28000,32000,34000,36000 | Field3 | Максимальная удельная листовая площадь: площадь на массу; X/Y задают функцию по стадии модели. |
| [Stem].MaximumNConc.XYPairs.Y = 0.05,0.045,0.02 | Field3 | Максимальная концентрация азота в органе по стадии; нет прямого азотного аналога AquaCrop. |
| [Stem].MaximumNConc.XYPairs.X = 3.0, 4.2, 5 | Field3 | Максимальная концентрация азота в органе по стадии; нет прямого азотного аналога AquaCrop. |
| [Oats].Structure.BranchMortality.MortalityPhase.Mortality.MortalityPerDegDay.XYPairs.Y[1] = 0.0008 | Field3 | Отмирание ветвей/побегов на единицу теплового времени; лишь косвенно связано с динамикой покрытия. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field3 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |

## Irgina

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].HeadEmergencePpSensitivity.FixedValue = 2.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].Phyllochron.BasePhyllochron.FixedValue = 50 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].ExtinctionCoeff.VegetativePhase.FixedValue = 0.8 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Phenology].PhyllochronPpSensitivity.FixedValue = 0.4 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].CAMP.FLNparams.MinLN = 5 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Минимальное конечное число листьев в CAMP. |
| [Phenology].CAMP.FLNparams.PpLN = 2.5 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр эффекта фотопериода на конечное число листьев CAMP. |
| [Phenology].CAMP.FLNparams.VrnLN = 7 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр эффекта яровизации на конечное число листьев CAMP; не срок зрелости AquaCrop. |
| [Phenology].CAMP.FLNparams.VxPLN = 0.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр взаимодействия яровизации и фотопериода в модели конечного числа листьев CAMP. |
| [Phenology].GrainFilling.Target.FixedValue = 225 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Целевое тепловое время фазы налива. Может обосновывать длительность формирования HI после согласования фаз и GDD. |
| [Phenology].GrainRipening.Target.FixedValue = 100 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Целевое тепловое время фазы дозревания. Входит в восстановление общей фенологии, отдельно не копируется. |

## LocalClover

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Leaf].Photosynthesis.FT.XYPairs.X = 0,4,9,15,18.5,23,27,30,37.5 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная функция фотосинтеза: X — температуры, Y — относительная реакция. Не переносится в Tbase/Tupper один к одному. |
| [Leaf].Photosynthesis.FT.XYPairs.Y = 0,0,0.1125,0.45,0.75,0.75,0.75,0.375,0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная функция фотосинтеза: X — температуры, Y — относительная реакция. Не переносится в Tbase/Tupper один к одному. |
| [Leaf].NRetranslocationFactor.TemperatureFactor.XYPairs.X = 0,4,9,15,18.5,23,27,30,37.5 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная реакция ремобилизации азота. В AquaCrop нет соответствующего органного азотного процесса. |
| [Leaf].NRetranslocationFactor.TemperatureFactor.XYPairs.Y = 0,0,0.15,0.6,1,1,1,0.5,0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная реакция ремобилизации азота. В AquaCrop нет соответствующего органного азотного процесса. |
| [Phenology].Inductive.Target.FixedValue = 15 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Цель индуктивной фазы фенологии; не самостоятельный параметр .CRO. |
| [Phenology].Reproductive.Target.FixedValue = 5000 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Цель репродуктивной фазы; не эквивалент числа календарных дней до зрелости. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Petiole].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Petiole].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Petiole].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Petiole].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Nodule].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Nodule].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Nodule].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Nodule].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |

## Mustard_sort

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].Vegetative.Target.XYPairs.X = 0, 25 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Функция цели вегетативной фазы APSIM. X и Y — точки функции; ось X определяется ресурсом модели. |
| [Phenology].Vegetative.Target.XYPairs.Y = 210, 1 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Функция цели вегетативной фазы APSIM. X и Y — точки функции; ось X определяется ресурсом модели. |
| [Phenology].StemElongation.Target.PPResponse.Amount.FixedValue = 180 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр цели/фотопериодического отклика фазы удлинения стебля. |
| [Leaf].Phyllochron.Base.FixedValue = 55 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |

## Rape_sort

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].Vegetative.Target.XYPairs.X = 0, 25 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Функция цели вегетативной фазы APSIM. X и Y — точки функции; ось X определяется ресурсом модели. |
| [Phenology].Vegetative.Target.XYPairs.Y = 290, 1 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Функция цели вегетативной фазы APSIM. X и Y — точки функции; ось X определяется ресурсом модели. |
| [Phenology].StemElongation.Target.PPResponse.Amount.FixedValue = 240 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр цели/фотопериодического отклика фазы удлинения стебля. |
| [Leaf].Phyllochron.Base.FixedValue = 65 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |

## TimothyProxy

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].ThermalTime.Response.X = 15.0, 34.5, 45.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная функция скорости накопления теплового времени. Требует согласования с методом GDD AquaCrop. |
| [Phenology].ThermalTime.Response.Y = 0.0, 20.0, 0.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная функция скорости накопления теплового времени. Требует согласования с методом GDD AquaCrop. |
| [Leaf].Photosynthesis.FT.XYPairs.X = 15, 17, 22, 28, 34.5, 42, 45 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная функция фотосинтеза: X — температуры, Y — относительная реакция. Не переносится в Tbase/Tupper один к одному. |
| [Leaf].Photosynthesis.FT.XYPairs.Y = 0, 0.0375, 0.225, 0.75, 0.75, 0.075, 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температурная функция фотосинтеза: X — температуры, Y — относительная реакция. Не переносится в Tbase/Tupper один к одному. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Inflorescence].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Root].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Taproot].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |

## Winter_rye_smoke

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].HeadEmergencePpSensitivity.FixedValue = 2.8 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].Phyllochron.BasePhyllochron.FixedValue = 98 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].ExtinctionCoeff.VegetativePhase.FixedValue = 0.85 | Field1, Field6, Field7, Field8, Field10 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Phenology].PhyllochronPpSensitivity.FixedValue = 0.2 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].CAMP.FLNparams.MinLN = 7.25 | Field1, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Минимальное конечное число листьев в CAMP. |
| [Phenology].CAMP.FLNparams.PpLN = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр эффекта фотопериода на конечное число листьев CAMP. |
| [Phenology].CAMP.FLNparams.VrnLN = 5.7 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр эффекта яровизации на конечное число листьев CAMP; не срок зрелости AquaCrop. |
| [Phenology].CAMP.FLNparams.VxPLN = 5.5 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр взаимодействия яровизации и фотопериода в модели конечного числа листьев CAMP. |
| [Phenology].GrainFilling.Target.FixedValue = 280 | Field1, Field6, Field7, Field8, Field10 | Целевое тепловое время фазы налива. Может обосновывать длительность формирования HI после согласования фаз и GDD. |
| [Phenology].GrainRipening.Target.FixedValue = 120 | Field1, Field6, Field7, Field8, Field10 | Целевое тепловое время фазы дозревания. Входит в восстановление общей фенологии, отдельно не копируется. |
| [Phenology].CAMP.EnvData.VrnTreatTemp = 5.8 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Температура яровизационной обработки в параметризации CAMP; отдельного аналога в .CRO нет. |
| [Phenology].CAMP.EnvData.VrnTreatDuration = 60 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Длительность яровизационной обработки CAMP; отдельного аналога в .CRO нет. |
| [Phenology].HeadEmergenceLongDayBase.FixedValue = 50 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Базовая длительность/скорость соответствующей фазы при длинном дне; конкретные единицы задаёт ресурс культуры APSIM. |
| [Leaf].ExtinctionCoeff.VegetativePhase.FixedValue = 0.82 | Field2, Field3, Field4, Field5, Field9 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Phenology].CAMP.FLNparams.MinLN = 8.4 | Field2 | Минимальное конечное число листьев в CAMP. |
| [Phenology].GrainFilling.Target.FixedValue = 390 | Field2, Field3, Field4, Field5, Field9 | Целевое тепловое время фазы налива. Может обосновывать длительность формирования HI после согласования фаз и GDD. |
| [Phenology].GrainRipening.Target.FixedValue = 165 | Field2, Field3, Field4, Field5, Field9 | Целевое тепловое время фазы дозревания. Входит в восстановление общей фенологии, отдельно не копируется. |

## Yakov

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].MinimumLeafNumber.FixedValue = 7 | Field1, Field2 | Минимальное число листьев в модели фенологии APSIM. |
| [Phenology].VrnSensitivity.FixedValue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологии к яровизации; в .CRO отдельного аналога нет. |
| [Phenology].PpSensitivity.FixedValue = 4.0 | Field1, Field2 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].EarlyReproductiveLongDayBase.FixedValue = 14.0 | Field1, Field2 | Базовая длительность/скорость соответствующей фазы при длинном дне; конкретные единицы задаёт ресурс культуры APSIM. |
| [Phenology].EarlyReproductivePpSensitivity.FixedValue = 3.5 | Field1, Field2 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Structure].Phyllochron.BasePhyllochron.FixedValue = 65 | Field1, Field2, Field3, Field4, Field5, Field9 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].ExtinctionCoeff.FixedValue = 0.4 | Field1, Field2 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Leaf].CohortParameters.MaxArea.AreaLargestLeaves.FixedValue = 4000 | Field1, Field2 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].CohortParameters.SenescenceDuration.Multiplier.FixedValue = 1.8 | Field1, Field2 | Множитель продолжительности старения листовой когорты; не равен CDC AquaCrop. |
| [Leaf].InitialLeaves[1].Area = 150 | Field1, Field2 | Площадь начального листа в когорте; для CC0 нужна общая площадь и густота. |
| [Leaf].CohortParameters.GrowthDuration.AgeFactor.XYPairs.Y = 1,1,0.1 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Возрастная функция продолжительности роста листа; не коэффициент CGC. |
| [Leaf].CohortParameters.MaxArea.AgeFactor.XYPairs.X = 3,3.5,4.0,4.5,4.9 | Field1, Field2 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].CohortParameters.MaxArea.AgeFactor.XYPairs.Y = 0.1,0.1,0.4,1,0.55 | Field1, Field2 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Structure].HeightModel.PotentialHeight.XYPairs.Y = 10,150,550,750 | Field1, Field2 | Функция потенциальной высоты; отдельной траектории высоты в текущем .CRO нет. |
| [Structure].BranchingRate.PotentialBranchingRate.Vegetative.PotentialBranchingRate.XYPairs.X = 1,2,3,4,5,6,7,8 | Field1, Field2 | Потенциальная скорость ветвления по фазам/состоянию; прямого аналога нет. |
| [Structure].BranchingRate.PotentialBranchingRate.Vegetative.PotentialBranchingRate.XYPairs.Y = 0,1,1,1,0.5,0,0,0 | Field1, Field2 | Потенциальная скорость ветвления по фазам/состоянию; прямого аналога нет. |
| [Oats].Structure.BranchingRate.StressFactors.CoverEffect.XYPairs.X = 1,1,1 | Field1, Field2 | Влияние покрытия на ветвление; не самостоятельный параметр .CRO. |
| [Oats].Leaf.CohortParameters.SpecificLeafAreaMax.XYPairs.X = 3,4.3,4.5,4.8 | Field1, Field2 | Максимальная удельная листовая площадь: площадь на массу; X/Y задают функцию по стадии модели. |
| [Oats].Leaf.CohortParameters.SpecificLeafAreaMax.XYPairs.Y = 22000,25000,27000,28000 | Field1, Field2 | Максимальная удельная листовая площадь: площадь на массу; X/Y задают функцию по стадии модели. |
| [Stem].MaximumNConc.XYPairs.X = 3.0, 4.2, 5 | Field1, Field2 | Максимальная концентрация азота в органе по стадии; нет прямого азотного аналога AquaCrop. |
| [Stem].MaximumNConc.XYPairs.Y = 0.05,0.045,0.02 | Field1, Field2 | Максимальная концентрация азота в органе по стадии; нет прямого азотного аналога AquaCrop. |
| [Grain].MaximumPotentialGrainSize.FixedValue = 0.045 | Field1, Field2 | Потенциальная масса зерновки; не HIo, вместе с числом зёрен ограничивает урожай APSIM. |
| [Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue = 18 | Field1, Field2 | Число зёрен на грамм стеблевой массы; не процент уборочного индекса. |
| [Oats].Structure.BranchMortality.MortalityPhase.Mortality.MortalityPerDegDay.XYPairs.Y[1] = 0.0008 | Field1, Field2 | Отмирание ветвей/побегов на единицу теплового времени; лишь косвенно связано с динамикой покрытия. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Phenology].MinimumLeafNumber.FixedValue = 6.5 | Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Минимальное число листьев в модели фенологии APSIM. |
| [Phenology].PpSensitivity.FixedValue = 3.5 | Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].EarlyReproductiveLongDayBase.FixedValue = 5.5 | Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Базовая длительность/скорость соответствующей фазы при длинном дне; конкретные единицы задаёт ресурс культуры APSIM. |
| [Phenology].EarlyReproductivePpSensitivity.FixedValue = 0.5 | Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Leaf].CohortParameters.MaxArea.AreaLargestLeaves.FixedValue = 1500 | Field3, Field4, Field5, Field9 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].CohortParameters.SenescenceDuration.Multiplier.FixedValue = 1.5 | Field3, Field4, Field5, Field9 | Множитель продолжительности старения листовой когорты; не равен CDC AquaCrop. |
| [Grain].MaximumPotentialGrainSize.FixedValue = 0.04 | Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Потенциальная масса зерновки; не HIo, вместе с числом зёрен ограничивает урожай APSIM. |
| [Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue = 20 | Field3, Field4, Field5, Field9 | Число зёрен на грамм стеблевой массы; не процент уборочного индекса. |
| [Leaf].ExtinctionCoeff.FixedValue = 0.5 | Field3, Field4, Field5, Field9 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Structure].Phyllochron.BasePhyllochron.FixedValue = 85 | Field6, Field7, Field8, Field10 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].CohortParameters.MaxArea.AreaLargestLeaves.FixedValue = 3500 | Field6, Field7, Field8, Field10 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].CohortParameters.SenescenceDuration.Multiplier.FixedValue = 2.5 | Field6, Field7, Field8, Field10 | Множитель продолжительности старения листовой когорты; не равен CDC AquaCrop. |
| [Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue = 45 | Field6, Field7, Field8, Field10 | Число зёрен на грамм стеблевой массы; не процент уборочного индекса. |
| [Leaf].ExtinctionCoeff.FixedValue = 0.7 | Field6, Field7, Field8, Field10 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |

## Yakov1

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].MinimumLeafNumber.FixedValue = 6.5 | Field1 | Минимальное число листьев в модели фенологии APSIM. |
| [Phenology].VrnSensitivity.FixedValue = 0 | Field1 | Чувствительность фенологии к яровизации; в .CRO отдельного аналога нет. |
| [Phenology].PpSensitivity.FixedValue = 3.5 | Field1 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].EarlyReproductiveLongDayBase.FixedValue = 5.5 | Field1 | Базовая длительность/скорость соответствующей фазы при длинном дне; конкретные единицы задаёт ресурс культуры APSIM. |
| [Phenology].EarlyReproductivePpSensitivity.FixedValue = 0.5 | Field1 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Structure].Phyllochron.BasePhyllochron.FixedValue = 85 | Field1 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].CohortParameters.MaxArea.AreaLargestLeaves.FixedValue = 3500 | Field1 | Параметры максимальной площади листьев и её распределения по возрасту/номеру листа; можно использовать для восстановления LAI. |
| [Leaf].CohortParameters.SenescenceDuration.Multiplier.FixedValue = 2.5 | Field1 | Множитель продолжительности старения листовой когорты; не равен CDC AquaCrop. |
| [Leaf].CohortParameters.GrowthDuration.AgeFactor.XYPairs.Y = 1,1,0.1 | Field1 | Возрастная функция продолжительности роста листа; не коэффициент CGC. |
| [Grain].MaximumPotentialGrainSize.FixedValue = 0.04 | Field1 | Потенциальная масса зерновки; не HIo, вместе с числом зёрен ограничивает урожай APSIM. |
| [Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue = 45 | Field1 | Число зёрен на грамм стеблевой массы; не процент уборочного индекса. |
| [Leaf].ExtinctionCoeff.FixedValue = 0.7 | Field1 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Leaf].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Stem].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionLiveToRemove = 0.9 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionDeadToRemove = 0.9 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionLiveToResidue = 0 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |
| [Panicle].BiomassRemovalDefaults.HarvestFractionDeadToResidue = 0 | Field1 | Доля живой/мёртвой массы органа, удаляемая (ToRemove) или оставляемая в остатках (ToResidue). Не равна HIo. |

## Zlata

| Команда и значение APSIM | Поля | Смысл и возможность переноса |
| --- | --- | --- |
| [Phenology].HeadEmergencePpSensitivity.FixedValue = 2.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].Phyllochron.BasePhyllochron.FixedValue = 75.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Филлохрон: тепловой интервал появления листьев либо его функция. В .CRO нет отдельного филлохрона. |
| [Leaf].ExtinctionCoeff.VegetativePhase.FixedValue = 0.8 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Коэффициент ослабления света пологом k. Вместе с LAI может помочь оценить CC; не равен CCx. |
| [Phenology].PhyllochronPpSensitivity.FixedValue = 0.4 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Чувствительность фенологической фазы/филлохрона к длине дня; прямого аналога в .CRO нет. |
| [Phenology].CAMP.FLNparams.MinLN = 5.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Минимальное конечное число листьев в CAMP. |
| [Phenology].CAMP.FLNparams.PpLN = 2.5 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр эффекта фотопериода на конечное число листьев CAMP. |
| [Phenology].CAMP.FLNparams.VrnLN = 7 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр эффекта яровизации на конечное число листьев CAMP; не срок зрелости AquaCrop. |
| [Phenology].CAMP.FLNparams.VxPLN = 0.0 | Field1, Field2, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Параметр взаимодействия яровизации и фотопериода в модели конечного числа листьев CAMP. |
| [Phenology].GrainFilling.Target.FixedValue = 250 | Field1, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Целевое тепловое время фазы налива. Может обосновывать длительность формирования HI после согласования фаз и GDD. |
| [Phenology].GrainRipening.Target.FixedValue = 100 | Field1, Field3, Field4, Field5, Field6, Field7, Field8, Field9, Field10 | Целевое тепловое время фазы дозревания. Входит в восстановление общей фенологии, отдельно не копируется. |
| [Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue = 13 | Field1, Field3, Field6, Field7, Field8, Field10 | Число зёрен на грамм стеблевой массы; не процент уборочного индекса. |
| [Phenology].GrainFilling.Target.FixedValue = 280 | Field2 | Целевое тепловое время фазы налива. Может обосновывать длительность формирования HI после согласования фаз и GDD. |
| [Phenology].GrainRipening.Target.FixedValue = 110 | Field2 | Целевое тепловое время фазы дозревания. Входит в восстановление общей фенологии, отдельно не копируется. |
| [Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue = 15 | Field2, Field4, Field5, Field9 | Число зёрен на грамм стеблевой массы; не процент уборочного индекса. |

## Параметры связи растения с почвой

`SoilCrop.LL` — нижний предел извлечения для конкретной культуры (м³/м³); `KL` — послойный коэффициент извлечения (1/сут); `XF` — ограничение доступа корней (0–1). Они отличаются от общепочвенного LL15. Текущий перенос использует LL15 в .SOL и не превращает KL/XF в параметры .CRO. Полные послойные значения каждого поля находятся в inventory.json.

`PlantainForage` с сортом `TimothyProxy` — модельный заменитель тимофеевки; `Wheat` с `Winter_rye_smoke` — заменитель озимой ржи. Наличие `Potato` среди доступных моделей поля не означает картофель в текущем севообороте.
