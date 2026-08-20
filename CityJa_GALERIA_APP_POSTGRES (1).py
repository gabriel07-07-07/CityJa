from flask import Flask, request, jsonify, send_from_directory, send_file
import os, re, base64, io
from secrets import token_urlsafe
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
app = Flask(__name__, static_folder=WEB, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não configurada no Render.")
    return psycopg2.connect(url)

def init_db():
    c = db(); cur = c.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS businesses(id SERIAL PRIMARY KEY,owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,name TEXT NOT NULL,category TEXT NOT NULL,city TEXT NOT NULL,address TEXT,description TEXT,lat DOUBLE PRECISION,lng DOUBLE PRECISION,phone TEXT,website TEXT,photo TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS reviews(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),text TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(user_id,business_id));
    CREATE TABLE IF NOT EXISTS promotions(id SERIAL PRIMARY KEY,business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,title TEXT NOT NULL,description TEXT,discount TEXT,valid_until TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS events(id SERIAL PRIMARY KEY,business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,title TEXT NOT NULL,description TEXT,event_date TEXT,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS notifications(id SERIAL PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,text TEXT,read INTEGER DEFAULT 0,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS boosts(id SERIAL PRIMARY KEY,business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,plan TEXT NOT NULL,status TEXT DEFAULT 'pending',created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS business_media(id SERIAL PRIMARY KEY,business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,media_type TEXT NOT NULL CHECK(media_type IN ('image','video')),filename TEXT,mime_type TEXT NOT NULL,data BYTEA NOT NULL,created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP);
    ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp TEXT;
    ALTER TABLE businesses ADD COLUMN IF NOT EXISTS hours TEXT;
    ALTER TABLE businesses ADD COLUMN IF NOT EXISTS price_range TEXT;
    """)
    c.commit(); cur.close(); c.close()

def migrate_main_photos_to_media():
    """Copia a foto principal antiga para a galeria uma única vez."""
    c = db(); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, photo FROM businesses WHERE photo IS NOT NULL AND photo LIKE 'data:image/%' AND photo <> ''")
    rows = cur.fetchall()
    for row in rows:
        cur.execute("SELECT 1 FROM business_media WHERE business_id=%s LIMIT 1", (row["id"],))
        if cur.fetchone():
            continue
        try:
            header, encoded = row["photo"].split(',', 1)
            mime = header.split(';', 1)[0].split(':', 1)[1]
            data = base64.b64decode(encoded)
            cur.execute("INSERT INTO business_media(business_id,media_type,filename,mime_type,data) VALUES(%s,'image',%s,%s,%s)",
                        (row["id"], "foto-principal.jpg", mime, psycopg2.Binary(data)))
        except Exception:
            c.rollback()
            # Re-open clean transaction and continue with the remaining stores.
            cur.close(); c.close()
            c = db(); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.commit(); cur.close(); c.close()

def auth():
    t=request.headers.get("Authorization","").replace("Bearer ","").strip()
    if not t:return None
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=%s",(t,)); u=cur.fetchone()
    cur.close(); c.close(); return u

@app.get("/")
def index(): return send_from_directory(BASE,"index.html")

@app.post("/api/register")
def register():
    d=request.get_json(force=True); name=(d.get("name") or "").strip(); email=(d.get("email") or "").strip().lower(); pw=d.get("password") or ""; role=d.get("role","user")
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email): return jsonify(error="Nome e e-mail válidos são obrigatórios."),400
    if len(pw)<6:return jsonify(error="A senha deve ter pelo menos 6 caracteres."),400
    if role not in ("user","merchant"):role="user"
    c=db(); cur=c.cursor()
    try:
        cur.execute("INSERT INTO users(name,email,password_hash,role) VALUES(%s,%s,%s,%s) RETURNING id",(name,email,generate_password_hash(pw),role)); uid=cur.fetchone()[0]
        t=token_urlsafe(32); cur.execute("INSERT INTO sessions(token,user_id) VALUES(%s,%s)",(t,uid)); c.commit()
        return jsonify(token=t,user={"id":uid,"name":name,"email":email,"role":role}),201
    except psycopg2.errors.UniqueViolation:
        c.rollback(); return jsonify(error="Este e-mail já está cadastrado."),409
    finally: cur.close(); c.close()

@app.post("/api/login")
def login():
    d=request.get_json(force=True); email=(d.get("email") or "").strip().lower(); pw=d.get("password") or ""
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM users WHERE email=%s",(email,)); u=cur.fetchone()
    if not u or not check_password_hash(u["password_hash"],pw): cur.close(); c.close(); return jsonify(error="E-mail ou senha incorretos."),401
    t=token_urlsafe(32); cur.execute("INSERT INTO sessions(token,user_id) VALUES(%s,%s)",(t,u["id"])); c.commit(); cur.close(); c.close()
    return jsonify(token=t,user={"id":u["id"],"name":u["name"],"email":u["email"],"role":u["role"]})

@app.get("/api/me")
def me():
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    return jsonify(user={"id":u["id"],"name":u["name"],"email":u["email"],"role":u["role"]})

@app.post("/api/logout")
def logout():
    t=request.headers.get("Authorization","").replace("Bearer ","").strip(); c=db(); cur=c.cursor(); cur.execute("DELETE FROM sessions WHERE token=%s",(t,)); c.commit(); cur.close(); c.close(); return jsonify(ok=True)

@app.get("/api/businesses")
def businesses():
    q=(request.args.get("q") or "").strip().lower(); city=(request.args.get("city") or "").strip().lower(); cat=(request.args.get("category") or "").strip().lower()
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,ROUND(COALESCE(AVG(r.rating),0),1) AS rating,COUNT(r.id) AS reviews FROM businesses b LEFT JOIN reviews r ON r.business_id=b.id GROUP BY b.id ORDER BY b.created_at DESC")
    data=cur.fetchall(); cur.close(); c.close(); out=[]
    for x in data:
        x=dict(x); hay=f"{x['name']} {x['category']} {x['city']} {x.get('description') or ''}".lower()
        if q and q not in hay:continue
        if city and city!=str(x['city']).lower():continue
        if cat and cat not in str(x['category']).lower():continue
        out.append(x)
    return jsonify(businesses=out)

@app.post("/api/businesses")
def create_business():
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Entre como comerciante para cadastrar um estabelecimento."),403
    d=request.get_json(force=True)
    if not d.get("name") or not d.get("category") or not d.get("city"):return jsonify(error="Nome, categoria e cidade são obrigatórios."),400
    c=db(); cur=c.cursor(); cur.execute("INSERT INTO businesses(owner_id,name,category,city,address,description,lat,lng,phone,website,photo,whatsapp,hours,price_range) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",(u["id"],d["name"],d["category"],d["city"],d.get("address",""),d.get("description",""),d.get("lat"),d.get("lng"),d.get("phone",""),d.get("website",""),d.get("photo",""),d.get("whatsapp",""),d.get("hours",""),d.get("price_range",""))); bid=cur.fetchone()[0]; c.commit(); cur.close(); c.close(); return jsonify(id=bid),201

@app.get("/api/businesses/<int:bid>")
def business_detail(bid):
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT b.*,ROUND(COALESCE(AVG(r.rating),0),1) AS rating,COUNT(r.id) AS reviews FROM businesses b LEFT JOIN reviews r ON r.business_id=b.id WHERE b.id=%s GROUP BY b.id",(bid,)); b=cur.fetchone()
    if not b:cur.close(); c.close(); return jsonify(error="Não encontrado."),404
    cur.execute("SELECT r.*,u.name FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.business_id=%s ORDER BY r.created_at DESC",(bid,)); rev=cur.fetchall()
    cur.execute("SELECT * FROM promotions WHERE business_id=%s ORDER BY created_at DESC",(bid,)); pro=cur.fetchall()
    cur.execute("SELECT id,business_id,media_type,filename,mime_type,created_at FROM business_media WHERE business_id=%s ORDER BY created_at DESC",(bid,)); media=cur.fetchall()
    cur.execute("SELECT * FROM events WHERE business_id=%s ORDER BY event_date",(bid,)); ev=cur.fetchall(); cur.close(); c.close()
    media_out=[]
    for m in media:
        m=dict(m); m["url"]="/api/media/"+str(m["id"]); media_out.append(m)
    return jsonify(business=dict(b),reviews=[dict(x) for x in rev],promotions=[dict(x) for x in pro],events=[dict(x) for x in ev],media=media_out)

@app.put("/api/businesses/<int:bid>")
def update_business(bid):
    u=auth()
    if not u or u["role"]!="merchant":
        return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True) or {}
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT owner_id FROM businesses WHERE id=%s",(bid,))
    owner=cur.fetchone()
    if not owner:
        cur.close(); c.close()
        return jsonify(error="Estabelecimento não encontrado."),404
    if int(owner["owner_id"]) != int(u["id"]):
        cur.close(); c.close()
        return jsonify(error="Você só pode editar sua própria loja."),403

    allowed=("name","category","city","address","description","phone","website","photo","whatsapp","hours","price_range")
    fields=[]; vals=[]
    for k in allowed:
        if k in d:
            if k=="photo" and d[k]:
                if not isinstance(d[k],str) or not d[k].startswith("data:image/"):
                    cur.close(); c.close()
                    return jsonify(error="Formato de foto inválido."),400
                if len(d[k])>3500000:
                    cur.close(); c.close()
                    return jsonify(error="A foto ficou muito grande. Escolha outra imagem."),400
            fields.append(k+"=%s"); vals.append(d[k])
    if not fields:
        cur.close(); c.close()
        return jsonify(error="Nenhuma alteração enviada."),400
    vals.append(bid)
    cur.execute("UPDATE businesses SET "+",".join(fields)+" WHERE id=%s",tuple(vals))
    c.commit(); cur.close(); c.close()
    return jsonify(ok=True,message="Estabelecimento atualizado com sucesso.")

def merchant_owns(bid, u):
    if not u or u["role"] != "merchant":
        return False
    c = db(); cur = c.cursor()
    cur.execute("SELECT 1 FROM businesses WHERE id=%s AND owner_id=%s", (bid, u["id"]))
    ok = cur.fetchone() is not None
    cur.close(); c.close()
    return ok

@app.get("/api/media/<int:mid>")
def media_file(mid):
    c = db(); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT mime_type,filename,data FROM business_media WHERE id=%s", (mid,))
    m = cur.fetchone(); cur.close(); c.close()
    if not m:
        return jsonify(error="Mídia não encontrada."), 404
    return send_file(io.BytesIO(bytes(m["data"])), mimetype=m["mime_type"], download_name=m["filename"] or "cityja-media", max_age=3600)

@app.get("/api/businesses/<int:bid>/media")
def list_media(bid):
    c = db(); cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id,business_id,media_type,filename,mime_type,created_at FROM business_media WHERE business_id=%s ORDER BY created_at DESC", (bid,))
    rows = cur.fetchall(); cur.close(); c.close()
    out=[]
    for x in rows:
        x=dict(x); x["url"]="/api/media/"+str(x["id"]); out.append(x)
    return jsonify(media=out)

@app.post("/api/businesses/<int:bid>/media")
def upload_media(bid):
    u=auth()
    if not merchant_owns(bid,u):
        return jsonify(error="Você só pode adicionar mídia à sua própria loja."),403
    f=request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Escolha uma foto ou vídeo."),400
    mime=(f.mimetype or "").lower()
    if mime.startswith("image/"):
        media_type="image"; max_bytes=5*1024*1024
    elif mime.startswith("video/"):
        media_type="video"; max_bytes=20*1024*1024
    else:
        return jsonify(error="Formato não suportado. Use imagem ou vídeo."),400
    data=f.read(max_bytes+1)
    if len(data)>max_bytes:
        return jsonify(error=("A foto deve ter até 5 MB." if media_type=="image" else "O vídeo deve ter até 20 MB.")),400
    c=db(); cur=c.cursor()
    cur.execute("INSERT INTO business_media(business_id,media_type,filename,mime_type,data) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (bid,media_type,f.filename,mime,psycopg2.Binary(data)))
    mid=cur.fetchone()[0]; c.commit(); cur.close(); c.close()
    return jsonify(ok=True,id=mid,url="/api/media/"+str(mid),media_type=media_type),201

@app.delete("/api/businesses/<int:bid>/media/<int:mid>")
def delete_media(bid,mid):
    u=auth()
    if not merchant_owns(bid,u):
        return jsonify(error="Você só pode remover mídia da sua própria loja."),403
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM business_media WHERE id=%s AND business_id=%s", (mid,bid))
    deleted=cur.rowcount; c.commit(); cur.close(); c.close()
    if not deleted:return jsonify(error="Mídia não encontrada."),404
    return jsonify(ok=True)

@app.post("/api/businesses/<int:bid>/reviews")
def review(bid):
    u=auth()
    if not u:return jsonify(error="Faça login para avaliar."),401
    d=request.get_json(force=True)
    try:rating=int(d.get("rating",0))
    except:rating=0
    if rating<1 or rating>5:return jsonify(error="Nota inválida."),400
    c=db(); cur=c.cursor(); cur.execute("INSERT INTO reviews(user_id,business_id,rating,text) VALUES(%s,%s,%s,%s)",(u["id"],bid,rating,(d.get("text") or "").strip())); c.commit(); cur.close(); c.close(); return jsonify(ok=True),201

@app.get("/api/favorites")
def favs():
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT b.* FROM businesses b JOIN favorites f ON f.business_id=b.id WHERE f.user_id=%s",(u["id"],)); r=cur.fetchall(); cur.close(); c.close(); return jsonify(businesses=[dict(x) for x in r])

@app.post("/api/favorites/<int:bid>")
def favorite(bid):
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    c=db(); cur=c.cursor(); cur.execute("SELECT 1 FROM favorites WHERE user_id=%s AND business_id=%s",(u["id"],bid)); ex=cur.fetchone()
    if ex:cur.execute("DELETE FROM favorites WHERE user_id=%s AND business_id=%s",(u["id"],bid)); state=False
    else:cur.execute("INSERT INTO favorites(user_id,business_id) VALUES(%s,%s)",(u["id"],bid)); state=True
    c.commit(); cur.close(); c.close(); return jsonify(favorite=state)

@app.post("/api/businesses/<int:bid>/promotions")
def promotion(bid):
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True); c=db(); cur=c.cursor(); cur.execute("INSERT INTO promotions(business_id,title,description,discount,valid_until) VALUES(%s,%s,%s,%s,%s)",(bid,d["title"],d.get("description",""),d.get("discount",""),d.get("valid_until",""))); c.commit(); cur.close(); c.close(); return jsonify(ok=True),201

@app.post("/api/businesses/<int:bid>/events")
def event(bid):
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True); c=db(); cur=c.cursor(); cur.execute("INSERT INTO events(business_id,title,description,event_date) VALUES(%s,%s,%s,%s)",(bid,d["title"],d.get("description",""),d.get("event_date",""))); c.commit(); cur.close(); c.close(); return jsonify(ok=True),201

@app.get("/api/events")
def events():
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT e.*,b.name AS business_name,b.city FROM events e JOIN businesses b ON b.id=e.business_id ORDER BY event_date"); r=cur.fetchall(); cur.close(); c.close(); return jsonify(events=[dict(x) for x in r])

@app.get("/api/promotions")
def promotions():
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT p.*,b.name AS business_name,b.city FROM promotions p JOIN businesses b ON b.id=p.business_id ORDER BY p.created_at DESC"); r=cur.fetchall(); cur.close(); c.close(); return jsonify(promotions=[dict(x) for x in r])

@app.post("/api/boosts/<int:bid>")
def boost(bid):
    u=auth()
    if not u or u["role"]!="merchant":return jsonify(error="Apenas comerciantes."),403
    d=request.get_json(force=True); c=db(); cur=c.cursor(); cur.execute("INSERT INTO boosts(business_id,plan,status) VALUES(%s,%s,%s)",(bid,d.get("plan","Destaque"),"pending")); c.commit(); cur.close(); c.close(); return jsonify(message="Solicitação de destaque registrada. O pagamento real será conectado na etapa de produção."),201

@app.get("/api/notifications")
def notifications():
    u=auth()
    if not u:return jsonify(error="Não autenticado."),401
    c=db(); cur=c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC",(u["id"],)); r=cur.fetchall(); cur.close(); c.close(); return jsonify(notifications=[dict(x) for x in r])

init_db()
migrate_main_photos_to_media()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
