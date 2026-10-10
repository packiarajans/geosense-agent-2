# train_model.py
# Purpose: Train and evaluate ML models (RandomForest + XGBoost) for site scoring.
#
# FIXES / NOTES:
#   * flood_risk is back in FEATURES. It only carries signal once you have run
#     dem_pipeline.py -> feature_engineering.py -> data_pipeline.sync so the
#     labelled_sites.csv flood_risk column actually varies. If it is still all 0
#     the models will just ignore it (harmless).
#   * Both models are balanced for the class imbalance
#     (RF class_weight='balanced', XGB scale_pos_weight).
#   * Model selection uses 5-fold CV ROC-AUC, and prints confusion matrix + report.
#   * Robust SHAP unpacking so the summary plot works for either model.
#   * Absolute save path, consistent with site_scorer.py.

import os
import pandas as pd
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
import xgboost as xgb
import shap
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE       = 'F:/GeoSense_Agent'
DATA_PATH  = f'{BASE}/data/processed/labelled_sites.csv'
MODEL_PATH = f'{BASE}/models/saved/site_scorer_model.pkl'
REPORT_DIR = f'{BASE}/outputs/reports'

FEATURES = ['dist_road_m', 'dist_hospital_m', 'flood_risk']
TARGET   = 'label'


def _positive_class_shap(explainer, X, model):
    """1-D-per-sample SHAP for the positive ('good'=1) class, any SHAP shape."""
    good_idx = list(model.classes_).index(1)
    sv = explainer.shap_values(X)
    if isinstance(sv, list):          # older SHAP: one array per class
        return np.asarray(sv[good_idx])
    sv = np.asarray(sv)
    if sv.ndim == 3:                  # (n_samples, n_features, n_classes)
        return sv[:, :, good_idx]
    return sv                         # (n_samples, n_features)


def train_and_evaluate():
    df = pd.read_csv(DATA_PATH)
    missing = [c for c in FEATURES + [TARGET] if c not in df.columns]
    if missing:
        raise KeyError(f'{DATA_PATH} is missing columns: {missing}. '
                       f'Re-run feature_engineering.py and data_pipeline.sync.')

    X = df[FEATURES]
    y = df[TARGET]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y)
    print(f'Training on {len(X_train)} samples, testing on {len(X_test)} samples')

    n_neg = int((y_train == 0).sum())
    n_pos = int((y_train == 1).sum())
    scale_pos_weight = n_neg / max(n_pos, 1)

    print('\nTraining Random Forest ...')
    rf = RandomForestClassifier(
        n_estimators=300, class_weight='balanced',
        random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)

    print('Training XGBoost ...')
    xg = xgb.XGBClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.1,
        subsample=0.9, colsample_bytree=0.9,
        scale_pos_weight=scale_pos_weight,
        random_state=42, eval_metric='logloss', n_jobs=-1)
    xg.fit(X_train, y_train)

    candidates = {'RandomForest': rf, 'XGBoost': xg}
    cv_scores = {}
    for name, mdl in candidates.items():
        cv = cross_val_score(mdl, X, y, cv=5, scoring='roc_auc', n_jobs=-1)
        cv_scores[name] = cv.mean()
        test_auc = roc_auc_score(y_test, mdl.predict_proba(X_test)[:, 1])
        print(f'\n=== {name} ===')
        print(f'  5-fold CV ROC-AUC: {cv.mean():.4f} +/- {cv.std():.4f}')
        print(f'  Held-out ROC-AUC : {test_auc:.4f}')
        print('  Confusion matrix (rows=true, cols=pred) [0=poor,1=good]:')
        print(confusion_matrix(y_test, mdl.predict(X_test)))
        print(classification_report(y_test, mdl.predict(X_test),
                                    target_names=['poor(0)', 'good(1)']))

    best_name = max(cv_scores, key=cv_scores.get)
    best = candidates[best_name]
    print(f'Best model (by CV ROC-AUC): {best_name}')

    print('Calculating SHAP feature importance ...')
    os.makedirs(REPORT_DIR, exist_ok=True)
    explainer = shap.TreeExplainer(best)
    shap_good = _positive_class_shap(explainer, X_test, best)
    shap.summary_plot(shap_good, X_test, feature_names=FEATURES, show=False)
    plt.savefig(f'{REPORT_DIR}/shap_importance.png', bbox_inches='tight')
    plt.close()
    print(f'SHAP plot saved to {REPORT_DIR}/shap_importance.png')

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(best, MODEL_PATH)
    print(f'Model saved to {MODEL_PATH}')

    if cv_scores[best_name] > 0.999:
        print('\n' + '!' * 68)
        print('WARNING: CV ROC-AUC ~= 1.0. The labels are almost certainly a')
        print('deterministic function of the features (e.g. dist_road_m < ~50 m).')
        print('The model is recovering the labelling rule, not learning site')
        print('quality. It will NOT generalise to real, unseen sites. Use labels')
        print('that are independent of the input features before trusting scores.')
        print('!' * 68)

    return best


if __name__ == '__main__':
    train_and_evaluate()
