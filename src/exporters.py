def export_data_to_csv(tests, filename="provas.csv"):
    import csv

    if not tests:
        print("Nenhum teste para exportar.")
        return

    keys = [
        "banca",
        "ano",
        "órgão",
        "cargo",
        "função",
        "aplicação",
        "escolaridade",
        "prova",
        "gabarito",
        "alterações",
        "edital"
    ]
    
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keys)
        writer.writeheader()
        writer.writerows(tests)


