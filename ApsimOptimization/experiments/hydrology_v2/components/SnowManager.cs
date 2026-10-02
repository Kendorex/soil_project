using System;
using Models.Core;
using Models.Climate;

namespace Models
{
    [Serializable]
    public class Script : Model
    {
        [Link] private Weather weather = null;
        public double SnowTemperature { get; set; } = -1.0;
        public double RainTemperature { get; set; } = 1.0;
        public double MeltTemperature { get; set; } = 0.0;
        public double DegreeDayFactor { get; set; } = 3.0;
        public double InitialSWE { get; set; } = 0.0;
        private const double MinLookupTemperature = -50.0;
        private const double MaxLookupTemperature = 50.0;
        private const double LookupStep = 0.1;
        private double[] snowFractionLookup;
        private double[] meltLookup;
        [Units("mm")] public double SWE { get; private set; }
        [Units("°C")] public double MeanTemperature { get; private set; }
        [Units("mm")] public double OriginalPrecipitation { get; private set; }
        [Units("mm")] public double Snowfall { get; private set; }
        [Units("mm")] public double LiquidRain { get; private set; }
        [Units("mm")] public double PotentialMelt { get; private set; }
        [Units("mm")] public double Melt { get; private set; }
        [Units("mm")] public double RainToApsim { get; private set; }
        public double SnowFraction { get; private set; }

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {
            SWE = InitialSWE;
            BuildLookupTables();
            weather.PreparingNewWeatherData += OnPreparingNewWeatherData;
        }

        private void BuildLookupTables()
        {
            if (RainTemperature <= SnowTemperature)
                throw new Exception("RainTemperature must be greater than SnowTemperature.");
            if (DegreeDayFactor < 0.0)
                throw new Exception("DegreeDayFactor cannot be negative.");
            int count = (int)Math.Round((MaxLookupTemperature - MinLookupTemperature) / LookupStep) + 1;
            snowFractionLookup = new double[count];
            meltLookup = new double[count];
            for (int i = 0; i < count; i++)
            {
                double temperature = MinLookupTemperature + i * LookupStep;
                if (temperature <= SnowTemperature) snowFractionLookup[i] = 1.0;
                else if (temperature >= RainTemperature) snowFractionLookup[i] = 0.0;
                else snowFractionLookup[i] = (RainTemperature - temperature) /
                                             (RainTemperature - SnowTemperature);
                meltLookup[i] = temperature <= MeltTemperature
                    ? 0.0 : DegreeDayFactor * (temperature - MeltTemperature);
            }
        }

        private int GetTemperatureIndex(double temperature)
        {
            temperature = Math.Max(MinLookupTemperature, Math.Min(MaxLookupTemperature, temperature));
            return (int)Math.Round((temperature - MinLookupTemperature) / LookupStep);
        }

        private void OnPreparingNewWeatherData(object sender, EventArgs e)
        {
            MeanTemperature = (weather.MaxT + weather.MinT) / 2.0;
            int index = GetTemperatureIndex(MeanTemperature);
            OriginalPrecipitation = weather.Rain;
            SnowFraction = snowFractionLookup[index];
            Snowfall = OriginalPrecipitation * SnowFraction;
            LiquidRain = OriginalPrecipitation - Snowfall;
            SWE += Snowfall;
            PotentialMelt = meltLookup[index];
            Melt = Math.Min(SWE, PotentialMelt);
            SWE = Math.Max(0.0, SWE - Melt);
            RainToApsim = LiquidRain + Melt;
            weather.Rain = RainToApsim;
        }

        [EventSubscribe("Completed")]
        private void OnCompleted(object sender, EventArgs e)
        {
            weather.PreparingNewWeatherData -= OnPreparingNewWeatherData;
        }
    }
}
