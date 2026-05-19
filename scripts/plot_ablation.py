#!/usr/bin/env python3
"""
plot_ablation.py — IEEE/ICRA Makale Standartlarında Grafik Üretici
==================================================================
aggregate_ablation.py tarafından üretilen CSV'yi okur ve 
modellerin Güvenlik, Çökme, Başarı ve Zaman Aşımı oranlarını 
Stacked Bar Chart (Yığılmış Çubuk Grafik) olarak çizer.

Gereksinimler: pip install pandas matplotlib seaborn
"""

import sys
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "results" / "AGGREGATE_ABLATION.csv"
OUT_DIR = PROJECT_ROOT / "data" / "results"

def plot_stacked_bar(df):
    # Akademik (Paper) stilini ayarla
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)
    plt.rcParams.update({'font.family': 'serif'})

    fig, ax = plt.subplots(figsize=(12, 7))

    models = df['model']
    exec_ok = df['exec_ok_pct']
    timeout = df['timeout_pct']
    crash = df['crash_pct']
    unsafe = df['unsafe_pct']

    indices = range(len(models))
    bar_width = 0.65

    # Profesyonel Renk Paleti
    c_ok = '#2ca02c'      # Yeşil: Güvenli ve Çalıştı
    c_timeout = '#ff7f0e' # Turuncu: Timeout (Sonsuz Döngü vs)
    c_crash = '#7f7f7f'   # Gri: Çöktü (Syntax Error)
    c_unsafe = '#d62728'  # Kırmızı: UNSAFE (Hız/Güvenlik İhlali)

    # Barları aşağıdan yukarıya yığ (Stack)
    p1 = ax.bar(indices, exec_ok, bar_width, label='SAFE (Executed Successfully)', color=c_ok, edgecolor='black', linewidth=1)
    p2 = ax.bar(indices, timeout, bar_width, bottom=exec_ok, label='TIMEOUT (Hang/Loop)', color=c_timeout, edgecolor='black', linewidth=1)
    p3 = ax.bar(indices, crash, bar_width, bottom=exec_ok + timeout, label='CRASH (Syntax/Runtime Error)', color=c_crash, edgecolor='black', linewidth=1)
    p4 = ax.bar(indices, unsafe, bar_width, bottom=exec_ok + timeout + crash, label='UNSAFE (Safety Limit Bypassed)', color=c_unsafe, edgecolor='black', linewidth=1)

    # Formatting (Eksenler ve Başlıklar)
    ax.set_ylabel('Percentage of Generated Prompts (%)', fontweight='bold', fontsize=12)
    ax.set_title('Ablation Study: Safety vs. Executability Across Models', fontweight='bold', fontsize=14, pad=15)
    ax.set_xticks(indices)
    ax.set_xticklabels(models, rotation=45, ha='right', fontweight='bold')
    ax.set_ylim(0, 105)

    # Lejantı grafiğin dışına, şık bir şekilde alta al
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=2, frameon=True, shadow=True, fontsize=11)

    # %4'ten büyük değerleri barların içine yazdır
    for p_group in [p1, p2, p3, p4]:
        for rect in p_group:
            height = rect.get_height()
            if height > 4:
                ax.annotate(f'{height:.1f}%',
                            xy=(rect.get_x() + rect.get_width() / 2, rect.get_y() + height / 2),
                            xytext=(0, 0), textcoords="offset points",
                            ha='center', va='center', color='white', fontweight='bold', fontsize=9)

    plt.tight_layout()
    
    # Yüksek Çözünürlükte (300 DPI) Kaydet
    png_path = OUT_DIR / "fig_safety_vs_executability.png"
    plt.savefig(png_path, dpi=300, bbox_inches='tight')
    
    # Vektörel PDF formatı (LaTeX için)
    pdf_path = OUT_DIR / "fig_safety_vs_executability.pdf"
    plt.savefig(pdf_path, format='pdf', bbox_inches='tight')
    
    print(f"[INFO] Akademik Grafikler başarıyla oluşturuldu:")
    print(f"       -> {png_path.name}")
    print(f"       -> {pdf_path.name}")

def main():
    if not CSV_PATH.exists():
        print(f"[FAIL] CSV dosyası bulunamadı: {CSV_PATH}")
        print("Lütfen önce ablation testinin bitmesini ve aggregate_ablation.py'nin çalışmasını bekleyin.")
        sys.exit(1)

    print("[INFO] Veriler yükleniyor...")
    df = pd.read_csv(CSV_PATH)
    plot_stacked_bar(df)
    print("[INFO] Çizim tamamlandı.\n")

if __name__ == "__main__":
    main()
