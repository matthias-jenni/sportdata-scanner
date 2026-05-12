import requests

with open("dummy.html", "w") as f:
    f.write("<html><body><table><tr class='dctabrowwhite'><td>Club(SUI)</td><td></td><td>John Doe</td><td></td><td></td><td></td><td>01 PF 091 V M +94 kg</td></tr></table></body></html>")

r = requests.post("http://127.0.0.1:5001/events/new", 
                  data={"event_name": "Test HTML"}, 
                  files={"registrations": ("dummy.html", open("dummy.html", "rb"), "text/html")},
                  allow_redirects=False)

print(r.status_code, r.headers)
