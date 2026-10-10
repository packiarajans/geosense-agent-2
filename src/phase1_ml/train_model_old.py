# train_model.py
# Purpose: Train and evaluate ML models for site scoring

import pandas as pd
import numpy as np
import joblib
import os
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
import shap
import matplotlib.pyplot as plt

FEATURES = ['dist_road_m','dist_hospital_m','flood_risk']
TARGET   = 'label'

def train_and_evaluate():
    # Load labelled data
    df = pd.read_csv('F:/GeoSense_Agent/data/processed/labelled_sites.csv')
    X = df[FEATURES]
    y = df[TARGET]

    # Split data: 80% for training, 20% for testing
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y)
    print(f'Training on {len(X_train)} samples, testing on {len(X_test)} samples')

    # Model 1: Random Forest
    print('Training Random Forest ...')
    rf = RandomForestClassifier(n_estimators=100, random_state=42)
    rf.fit(X_train, y_train)
    rf_score = rf.score(X_test, y_test)
    print(f'Random Forest Accuracy: {rf_score:.3f}')
    print(classification_report(y_test, rf.predict(X_test)))

    # Model 2: XGBoost
    print('Training XGBoost ...')
    xg = xgb.XGBClassifier(n_estimators=100, random_state=42, eval_metric='logloss')
    xg.fit(X_train, y_train)
    xg_score = xg.score(X_test, y_test)
    print(f'XGBoost Accuracy: {xg_score:.3f}')

    # Use the better model
    best = rf if rf_score >= xg_score else xg
    best_name = 'RandomForest' if rf_score >= xg_score else 'XGBoost'
    print(f'Best model: {best_name}')

    # SHAP explanations — understand WHY the model scores each location
    print('Calculating SHAP feature importance ...')
    explainer = shap.TreeExplainer(best)
    shap_vals = explainer.shap_values(X_test)
    shap.summary_plot(shap_vals, X_test, feature_names=FEATURES, show=False)
    plt.savefig('F:/GeoSense_Agent/outputs/reports/shap_importance.png', bbox_inches='tight')
    plt.close()
    print('SHAP plot saved to outputs/reports/')

    # Save the best model
    os.makedirs('F:/GeoSense_Agent/models/saved', exist_ok=True)
    joblib.dump(best, 'models/saved/site_scorer_model.pkl')
    print('Model saved to models/saved/site_scorer_model.pkl')
    return best

if __name__ == '__main__':
    train_and_evaluate() 