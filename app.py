from flask import Flask, request, jsonify, send_from_directory
import sqlite3, os, re
from werkzeug.security import generate_password_hash, check_password_hash
from secrets import token_urlsafe

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "cityja.db")
WEB = os.path.join(BASE, "web")

app = Flask(__name__, static_folder=WEB, static_url_path="")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS businesses(id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER NOT NULL,name TEXT NOT NULL,category TEXT NOT NULL,city TEXT NOT NULL,address TEXT,description TEXT,lat REAL,lng REAL,phone TEXT,website TEXT,photo TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,business_id INTEGER NOT NULL,rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),text TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL,business_id INTEGER NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(user_id,business_id));
    CREATE TABLE IF NOT EXISTS promotions(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,title TEXT NOT NULL,description TEXT,discount TEXT,valid_until TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,title TEXT NOT NULL,description TEXT,event_date TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,title TEXT NOT NULL,text TEXT,read INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS boosts(id INTEGER PRIMARY KEY AUTOINCREMENT,business_id INTEGER NOT NULL,plan TEXT NOT NULL,status TEXT DEFAULT 'pending',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    """)
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        uid = c.execute(
            "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
            ("CityJá Demo","demo@cityja.local",generate_password_hash("cityja123"),"merchant")
        ).lastrowid
        samples = [
            ("Bar do Zé","Bar","Vitória","Praia do Canto","Música ao vivo, happy hour, jogos e ambiente descontraído.",-20.292,-40.292),
            ("Restaurante Sabor","Restaurante","Vitória","Jardim da Penha","Buffet, estacionamento e opções para família.",-20.272,-40.293),
            ("Café Central","Café","Vila Velha","Praia da Costa","Café especial, brunch e espaço pet friendly.",-20.331,-40.286)
        ]
        for n,cat,city,addr,desc,lat,lng in samples:
            c.execute(
                "INSERT INTO businesses(owner_id,name,category,city,address,description,lat,lng) VALUES(?,?,?,?,?,?,?,?)",
                (uid,n,cat,city,addr,desc,lat,lng)
            )
        bid = c.execute("SELECT id FROM businesses WHERE name='Bar do Zé'").fetchone()[0]
        c.execute(
            "INSERT INTO promotions(business_id,title,description,discount,valid_until) VALUES(?,?,?,?,?)",
            (bid,"Happy Hour","Chopp + petisco de segunda a quinta","20%","2026-12-31")
        )
        c.execute(
            "INSERT INTO events(business_id,title,description,event_date) VALUES(?,?,?,?)",
            (bid,"Música ao vivo","Sexta com banda local","2026-09-04 20:00")
        )
    c.commit()
    c.close()

def auth():
    t = request.headers.get("Authorization","").replace("Bearer ","").strip()
    if not t:
        return None
    c = db()
    u = c.execute(
        "SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=?",(t,)
    ).fetchone()
    c.close()
    return u

@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")

@app.post("/api/register")
def register():
    d=request.get_json(force=True); name=(d.get("name") or "").strip(); email=(d.get("email") or "").strip().lower(); pw=d.get("password") or ""; role=d.get("role","user")
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email): return jsonify(error="Nome e e-mail válidos são obrigatórios."),400
    if len(pw)<6:return jsonify(error="A senha deve ter pelo menos 6 caracteres."),400
    if role not in ("user","merchant"):role="user"
    c=db()
    try:
        uid=c.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",(name,email,generate_password_hash(pw),role)).lastrowid
        t=token_urlsafe(32);c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)",(t,uid));c.commit()
        return jsonify(token=t,user={"id":uid,"name":name,"email":email,"role":role}),201
    except sqlite3.IntegrityError:return jsonify(error="Este e-mail já está cadastrado."),409
    finally:c.close()

@app.post("/api/login")
def login():
    d=request.get_json(force=True); email=(d.get("email") or "").strip().lower();pw=d.get("password") or ""
    c=db();u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    if not u or not check_password_hash(u["password_hash"],pw):c.close();return jsonify(error="E-mail ou senha incorretos."),401
    t=token_urlsafe(32);c.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)",(t,u["id"]));c.commit();c.close()
    return jsonify(token=t,user={"id":u["id"],"name":u["name"],"email":u["email"],"role":u["role"]})

@app.get("/api/me")
def me():
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    return jsonify(user=dict(id=u["id"],name=u["name"],email=u["email"],role=u["role"]))

@app.post("/api/logout")
def logout():
    t=request.headers.get("Authorization","").replace("Bearer ","").strip();c=db();c.execute("DELETE FROM sessions WHERE token=?",(t,));c.commit();c.close();return jsonify(ok=True)

@app.get("/api/businesses")
def businesses():
    q=(request.args.get("q") or "").strip().lower();city=(request.args.get("city") or "").strip().lower();cat=(request.args.get("category") or "").strip().lower()
    c=db()
    rows=c.execute("""SELECT b.*,ROUND(COALESCE(AVG(r.rating),0),1) rating,COUNT(r.id) reviews
                      FROM businesses b LEFT JOIN reviews r ON r.business_id=b.id GROUP BY b.id ORDER BY b.created_at DESC""").fetchall()
    c.close();out=[]
    for r in rows:
        x=dict(r);hay=(x["name"]+" "+x["category"]+" "+x["city"]+" "+(x["description"] or "")).lower()
        if q and q not in hay:continue
        if city and city!=x["city"].lower():continue
        if cat and cat not in x["category"].lower():continue
        out.append(x)
    return jsonify(businesses=out)

@app.post("/api/businesses")
def create_business():
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Entre como comerciante para cadastrar um estabelecimento."),403
    d=request.get_json(force=True)
    if not d.get("name") or not d.get("category") or not d.get("city"):return jsonify(error="Nome, categoria e cidade são obrigatórios."),400
    c=db();bid=c.execute("""INSERT INTO businesses(owner_id,name,category,city,address,description,lat,lng,phone,website,photo)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(u["id"],d["name"],d["category"],d["city"],d.get("address",""),d.get("description",""),d.get("lat"),d.get("lng"),d.get("phone",""),d.get("website",""),d.get("photo",""))).lastrowid
    c.commit();c.close();return jsonify(id=bid),201

@app.get("/api/businesses/<int:bid>")
def business_detail(bid):
    c=db();b=c.execute("""SELECT b.*,ROUND(COALESCE(AVG(r.rating),0),1) rating,COUNT(r.id) reviews FROM businesses b LEFT JOIN reviews r ON r.business_id=b.id WHERE b.id=? GROUP BY b.id""",(bid,)).fetchone()
    if not b:c.close();return jsonify(error="Não encontrado."),404
    rev=c.execute("SELECT r.*,u.name FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.business_id=? ORDER BY r.created_at DESC",(bid,)).fetchall()
    pro=c.execute("SELECT * FROM promotions WHERE business_id=? ORDER BY created_at DESC",(bid,)).fetchall()
    ev=c.execute("SELECT * FROM events WHERE business_id=? ORDER BY event_date",(bid,)).fetchall()
    c.close();return jsonify(business=dict(b),reviews=[dict(x) for x in rev],promotions=[dict(x) for x in pro],events=[dict(x) for x in ev])

@app.post("/api/businesses/<int:bid>/reviews")
def review(bid):
    u=auth()
    if not u:return jsonify(error="Faça login para avaliar."),401
    d=request.get_json(force=True);rating=int(d.get("rating",0))
    if rating<1 or rating>5:return jsonify(error="Nota inválida."),400
    c=db();c.execute("INSERT INTO reviews(user_id,business_id,rating,text) VALUES(?,?,?,?)",(u["id"],bid,rating,(d.get("text") or "").strip()));c.commit();c.close();return jsonify(ok=True),201

@app.get("/api/favorites")
def favs():
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    c=db();r=c.execute("SELECT b.* FROM businesses b JOIN favorites f ON f.business_id=b.id WHERE f.user_id=?",(u["id"],)).fetchall();c.close();return jsonify(businesses=[dict(x) for x in r])

@app.post("/api/favorites/<int:bid>")
def favorite(bid):
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    c=db();ex=c.execute("SELECT 1 FROM favorites WHERE user_id=? AND business_id=?",(u["id"],bid)).fetchone()
    if ex:c.execute("DELETE FROM favorites WHERE user_id=? AND business_id=?",(u["id"],bid));state=False
    else:c.execute("INSERT INTO favorites(user_id,business_id) VALUES(?,?)",(u["id"],bid));state=True
    c.commit();c.close();return jsonify(favorite=state)

@app.post("/api/businesses/<int:bid>/promotions")
def promotion(bid):
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True);c=db();c.execute("INSERT INTO promotions(business_id,title,description,discount,valid_until) VALUES(?,?,?,?,?)",(bid,d["title"],d.get("description",""),d.get("discount",""),d.get("valid_until","")));c.commit();c.close();return jsonify(ok=True),201

@app.post("/api/businesses/<int:bid>/events")
def event(bid):
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True);c=db();c.execute("INSERT INTO events(business_id,title,description,event_date) VALUES(?,?,?,?)",(bid,d["title"],d.get("description",""),d.get("event_date","")));c.commit();c.close();return jsonify(ok=True),201

@app.get("/api/events")
def events():
    c=db();r=c.execute("SELECT e.*,b.name business_name,b.city FROM events e JOIN businesses b ON b.id=e.business_id ORDER BY event_date").fetchall();c.close();return jsonify(events=[dict(x) for x in r])

@app.get("/api/promotions")
def promotions():
    c=db();r=c.execute("SELECT p.*,b.name business_name,b.city FROM promotions p JOIN businesses b ON b.id=p.business_id ORDER BY p.created_at DESC").fetchall();c.close();return jsonify(promotions=[dict(x) for x in r])

@app.post("/api/boosts/<int:bid>")
def boost(bid):
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True);c=db();c.execute("INSERT INTO boosts(business_id,plan,status) VALUES(?,?,?)",(bid,d.get("plan","Destaque"),"pending"));c.commit();c.close();return jsonify(message="Solicitação de destaque registrada. O pagamento real será conectado na etapa de produção."),201

@app.get("/api/notifications")
def notifications():
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    c=db();r=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",(u["id"],)).fetchall();c.close();return jsonify(notifications=[dict(x) for x in r])

init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=True)
