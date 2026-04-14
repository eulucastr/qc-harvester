import time

import cloudscraper
from bs4 import BeautifulSoup


# Dentro da página da provas, resgata cada um dos links relacionados à um cargo
def get_roles(main_url):
    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(main_url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {main_url}: {e}")
    soup = BeautifulSoup(response.text, "html.parser")

    tests = []
    # for link in soup.select("#provas .link-i a"):
    #     href = link.get("href")
    #     role_tests = get_exams(href)
    #     for test in role_tests:
    #         tests.append(test)
    role_tests = get_exams("https://www.pciconcursos.com.br/provas/analista-de-suporte")

    return tests


# Dentro da página de cada cargo, resgata os links das páginas
# de listagem de concursos relacionados à esse cargo. Essas listas
# possuem paginação no modelo de url:
# https://www.pciconcursos.com.br/provas/adminstração
# https://www.pciconcursos.com.br/provas/adminstração/2
# https://www.pciconcursos.com.br/provas/adminstração/3
# ...
def get_exams(role_url):
    scraper = cloudscraper.create_scraper()

    role_tests = []
    end_loop = False
    count = 1
    while not end_loop:
        if count == 1:
            page_url = role_url
        else:
            page_url = role_url.rstrip("/") + f"/{count}"

        try:
            response = scraper.get(page_url, timeout=10)
            response.raise_for_status()
        except Exception as e:
            raise RuntimeError(f"Erro ao acessar {page_url}: {e}")

        soup = BeautifulSoup(response.text, "html.parser")

        print(f"Acessando PAGINAÇÃO {page_url}...")
        for p_tag in soup.find_all("p"):
            p_text = p_tag.get_text(strip=True)
            if p_text and "nenhuma prova encontrada" in p_text.lower():
                end_loop = True
                break
        if end_loop:
            break

        links = soup.select("a.prova_download")
        if not links:
            end_loop = True
            break

        for link in links:
            href = link.get("href")
            if not href:
                continue
            test = get_test(href)
            role_tests.append(test)

        count += 1

    return role_tests


# Dentro da página do concurso, regasta o link da prova e do gabarito relacionados
def get_test(url):
    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Erro ao acessar {url}: {e}")
    soup = BeautifulSoup(response.text, "html.parser")

    test = {}

    list_items = soup.select("#download .card-body ul li")
    for li in list_items:
        text = li.get_text(strip=True)

        if "Cargo:" in text:
            test["cargo"] = text.replace("Cargo:", "").strip()
        elif "Ano:" in text:
            test["ano"] = text.replace("Ano:", "").strip()
        elif "Órgão:" in text:
            test["entidade"] = text.replace("Órgão:", "").strip()
        elif "Organizadora:" in text:
            test["banca"] = text.replace("Organizadora:", "").strip()

    download_card = None
    for card in soup.select("#download .card"):
        h5 = card.select_one(".card-header h5")
        if h5 and "Download" in h5.get_text():
            download_card = card
            break

    if download_card:
        download_links = download_card.select(".item-link")
        for link in download_links:
            href = link.get("href")
            text_content = link.get_text(strip=True).lower()

            if href:
                if "prova" in text_content:
                    test["prova"] = href
                elif "gab" in text_content or "gabarito" in text_content:
                    test["gabarito"] = href
    print(test)               
    return test


def export_tests_to_csv(tests, filename="provas.csv"):
    import csv

    if not tests:
        print("Nenhum teste para exportar.")
        return

    keys = tests[0].keys()
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keys)
        writer.writeheader()
        writer.writerows(tests)


def main():
    main_url = "https://www.pciconcursos.com.br/provas"
    tests = get_roles(main_url)
    export_tests_to_csv(tests)


if __name__ == "__main__":
    main()
