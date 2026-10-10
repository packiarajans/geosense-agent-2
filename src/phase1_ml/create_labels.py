# create_labels.py
# Purpose: Turn features.csv into labelled_sites.csv using the labelling rule
#          from the Phase-1 manual (Section E.4.1 / "TIP: Creating Labels").
#
# Manual rule:
#   Good site (label=1): low flood risk AND near a road AND near a hospital
#                        (AND medium/high population)
#   Bad site  (label=0): high flood risk OR very remote OR no nearby services
#
# NOTE on population: the manual also lists population, but Phase-1 has no
# population layer loaded, so this script uses the three features we actually
# compute (flood_risk, dist_road_m, dist_hospital_m). Add a population term here
# later if/when you load that data. All thresholds are constants below so you
# can tune them to match your domain judgement.
#
# Run (from project root, after feature_engineering.py):
#   python -m src.phase1_ml.create_labels

import pandas as pd

BASE         = 'F:/GeoSense_Agent'
FEATURES_CSV = f'{BASE}/data/processed/features.csv'
LABELS_CSV   = f'{BASE}/data/processed/labelled_sites.csv'

# --- Thresholds (tune to taste) --------------------------------------------
NEAR_ROAD_M     = 50.0     # "near a road"
NEAR_HOSPITAL_M = 1000.0   # "near a hospital / services" (within 1 km)
LOW_FLOOD_MAX   = 2.0      # flood_risk 0-10: 0=none, 2=low, 5=medium, 8=high
                           # -> "low flood risk" means flood_risk <= 2


def make_labels(features_csv=FEATURES_CSV, labels_csv=LABELS_CSV):
    df = pd.read_csv(features_csv)
    for col in ('dist_road_m', 'dist_hospital_m', 'flood_risk'):
        if col not in df.columns:
            raise KeyError(f'{features_csv} is missing "{col}". '
                           f'Run feature_engineering.py first.')

    low_flood    = df['flood_risk']      <= LOW_FLOOD_MAX
    near_road    = df['dist_road_m']     <= NEAR_ROAD_M
    near_hospital = df['dist_hospital_m'] <= NEAR_HOSPITAL_M

    # Good = all positive criteria met; Bad = anything else
    # (the negation is exactly "high flood OR remote OR no services").
    df['label'] = (low_flood & near_road & near_hospital).astype(int)

    df.to_csv(labels_csv, index=False)

    n_good = int(df['label'].sum())
    n_bad  = len(df) - n_good
    print(f'Labelling rule: low_flood(<= {LOW_FLOOD_MAX}) '
          f'AND near_road(<= {NEAR_ROAD_M} m) '
          f'AND near_hospital(<= {NEAR_HOSPITAL_M} m)')
    print(f'Labelled {len(df)} sites -> good(1): {n_good}, bad(0): {n_bad}')
    if n_good < 150 or n_bad < 150:
        print('NOTE: the manual suggests aiming for >=150 of each class. '
              'Adjust the thresholds above if one class is too small.')
    print(f'Saved to {labels_csv}')
    return df


if __name__ == '__main__':
    make_labels()
