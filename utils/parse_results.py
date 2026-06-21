import re
from bs4 import BeautifulSoup
import traceback

def extract_results_html(filepath: str) -> list[dict]:
    results = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html_content = f.read()
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # The main results table has id "ft" or just class "dctable"
        # We can also just iterate over rows that have class dctabrowwhite or dctabrowgreen
        rows = soup.find_all('tr', class_=['dctabrowwhite', 'dctabrowgreen'])
        
        for tr in rows:
            tds = tr.find_all('td')
            if len(tds) < 6:
                continue
                
            cat_text = tds[0].get_text(separator=" ", strip=True)            
            # Placement is usually in a div like <div class="numberCircle_gold">1</div>
            place_text = tds[1].get_text(strip=True)
            if not place_text.isdigit():
                continue
            
            placement = int(place_text)
            name = tds[2].get_text(strip=True)
            club = tds[4].get_text(strip=True)
            
            # Country: sometimes GREECE, sometimes GRE. Let's grab the text
            country = tds[5].get_text(strip=True)
            # Try to grab from the image src if it exists (e.g., aut.png -> AUT)
            img_tag = tds[5].find('img')
            if img_tag and img_tag.has_attr('src'):
                src = img_tag['src'].lower()
                m = re.search(r'([a-z]{3})\.png$', src)
                if m:
                    country = m.group(1).upper()
            
            cat_code = re.sub(r'^\s+', '', cat_text) 
            cat_code = re.sub(r'\s+', ' ', cat_code).strip()
            
            results.append({
                "category_code": cat_code,
                "placement": placement,
                "name": name,
                "club": club,
                "country": country
            })
            
    except Exception as e:
        print(f"Failed to parse results HTML: {e}")
        traceback.print_exc()
        
    # Group results by category
    
    return results

if __name__ == "__main__":
    import json
    data = extract_results_html("test-data/results.html")
    print(f"Extracted {len(data)} results")
    print(json.dumps(data[:3], indent=2))
