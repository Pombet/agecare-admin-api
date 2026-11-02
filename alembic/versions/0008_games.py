"""Catálogo y permisos exclusivos de juegos, por organización.

Revision ID: 0008_games
Revises: 0007_security_retention
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_games'
down_revision = '0007_security_retention'
branch_labels = None
depends_on = None

# Catálogo inicial congelado abajo. Todos se instalan inactivos: cada
# organización elige su publicación. No se ejecuta scripts.seed.
INITIAL_GAMES = [{'slug': 'rompecabezas',
  'title': 'Rompecabezas',
  'description': 'Arma la imagen tocando dos piezas para intercambiarlas.',
  'category': 'Percepción',
  'target_url': '/static/juegos/rompecabezas/index.html'},
 {'slug': 'memorama',
  'title': 'Memorama',
  'description': 'Encuentra las parejas de cartas iguales. Tres temas rotativos.',
  'category': 'Memoria',
  'target_url': '/static/juegos/memorama/index.html'},
 {'slug': 'diferencias',
  'title': 'Buscar diferencias',
  'description': 'Encuentra los detalles que cambian entre las dos escenas.',
  'category': 'Atención',
  'target_url': '/static/juegos/diferencias/index.html'},
 {'slug': 'pintar',
  'title': 'Pintar',
  'description': 'Elige un color y toca el dibujo para pintarlo. Cuatro diseños.',
  'category': 'Bienestar',
  'target_url': '/static/juegos/pintar/index.html'},
 {'slug': 'trivia',
  'title': 'Trivia nostálgica',
  'description': 'Preguntas sencillas sobre recuerdos de otras épocas.',
  'category': 'Memoria',
  'target_url': '/static/juegos/trivia/index.html'},
 {'slug': 'bingo',
  'title': 'Bingo',
  'description': 'Marca en tu cartón los números que se van cantando en voz alta.',
  'category': 'Atención',
  'target_url': '/static/juegos/bingo/index.html'},
 {'slug': 'sonido-imagen',
  'title': 'Emparejar sonido e imagen',
  'description': 'Escucha el sonido y toca la imagen que corresponde.',
  'category': 'Atención',
  'target_url': '/static/juegos/sonido-imagen/index.html'},
 {'slug': 'jardin',
  'title': 'Mi jardín',
  'description': 'Riega tu planta y obsérvala crecer, sin apuro y sin errores posibles.',
  'category': 'Bienestar',
  'target_url': '/static/juegos/jardin/index.html'},
 {'slug': 'sopa-letras',
  'title': 'Sopa de letras',
  'description': 'Encuentra las palabras tocando su primera y su última letra.',
  'category': 'Lenguaje',
  'target_url': '/static/juegos/sopa-letras/index.html'},
 {'slug': 'ordenar-secuencias',
  'title': 'Ordenar secuencias',
  'description': 'Ordena los pasos de una actividad tocando dos para intercambiarlos.',
  'category': 'Lógica',
  'target_url': '/static/juegos/ordenar-secuencias/index.html'},
 {'slug': 'domino',
  'title': 'Dominó tranquilo',
  'description': 'Une fichas con números iguales, a tu ritmo.',
  'category': 'Dominó',
  'target_url': '/static/juegos/domino/index.html'},
 {'slug': 'lista-compras',
  'title': 'Mi lista de compras',
  'description': 'Recuerda una lista y encuentra sus productos.',
  'category': 'Memoria',
  'target_url': '/static/juegos/lista-compras/index.html'},
 {'slug': 'palabras-silabas',
  'title': 'Palabras por sílabas',
  'description': 'Construye palabras tocando sus sílabas.',
  'category': 'Lenguaje',
  'target_url': '/static/juegos/palabras-silabas/index.html'},
 {'slug': 'cada-cosa',
  'title': 'Cada cosa en su lugar',
  'description': 'Elige el grupo al que pertenece cada objeto.',
  'category': 'Asociación',
  'target_url': '/static/juegos/cada-cosa/index.html'},
 {'slug': 'camino-casa',
  'title': 'Camino a casa',
  'description': 'Encuentra un camino en un laberinto diferente en cada ronda.',
  'category': 'Orientación',
  'target_url': '/static/juegos/camino-casa/index.html'},
 {'slug': 'continua-patron',
  'title': 'Continúa el patrón',
  'description': 'Descubre qué figura sigue en la serie.',
  'category': 'Lógica',
  'target_url': '/static/juegos/continua-patron/index.html'},
 {'slug': 'reloj',
  'title': 'Pon el reloj en hora',
  'description': 'Ajusta las agujas para representar la hora indicada.',
  'category': 'Vida cotidiana',
  'target_url': '/static/juegos/reloj/index.html'},
 {'slug': 'cuenta-elige',
  'title': 'Cuenta y elige',
  'description': 'Cuenta los objetos y elige su cantidad.',
  'category': 'Atención',
  'target_url': '/static/juegos/cuenta-elige/index.html'},
 {'slug': 'tres-raya',
  'title': 'Tres en raya',
  'description': 'Forma una línea de tres símbolos sin prisa.',
  'category': 'Estrategia',
  'target_url': '/static/juegos/tres-raya/index.html'},
 {'slug': 'mi-paisaje',
  'title': 'Mi paisaje',
  'description': 'Construye una escena colocando elementos a tu gusto.',
  'category': 'Creatividad',
  'target_url': '/static/juegos/mi-paisaje/index.html'},
 {'slug': 'sudoku',
  'title': 'Sudoku sencillo',
  'description': 'Completa un tablero de 4 por 4 con números del 1 al 4.',
  'category': 'Lógica',
  'target_url': '/static/juegos/sudoku/index.html'},
 {'slug': 'torres-hanoi',
  'title': 'Torres de Hanói',
  'description': 'Mueve los discos entre torres, uno a uno y sin poner uno grande sobre uno '
                 'pequeño.',
  'category': 'Lógica',
  'target_url': '/static/juegos/torres-hanoi/index.html'},
 {'slug': 'balanza',
  'title': 'Equilibra la balanza',
  'description': 'Combina pesas para conseguir el mismo peso en ambos lados.',
  'category': 'Cálculo',
  'target_url': '/static/juegos/balanza/index.html'},
 {'slug': 'secuencia-luces',
  'title': 'Secuencia de luces',
  'description': 'Observa las figuras iluminadas a tu ritmo y repite el orden.',
  'category': 'Memoria',
  'target_url': '/static/juegos/secuencia-luces/index.html'},
 {'slug': 'bolos',
  'title': 'Bolos tranquilos',
  'description': 'Elige dirección y fuerza para derribar los bolos en dos lanzamientos.',
  'category': 'Coordinación',
  'target_url': '/static/juegos/bolos/index.html'},
 {'slug': 'une-puntos',
  'title': 'Une los puntos',
  'description': 'Toca los números en orden y descubre un dibujo, sin arrastrar.',
  'category': 'Percepción',
  'target_url': '/static/juegos/une-puntos/index.html'},
 {'slug': 'refranes',
  'title': 'Completa el refrán',
  'description': 'Elige la expresión que completa un refrán conocido y descubre su significado.',
  'category': 'Lenguaje',
  'target_url': '/static/juegos/refranes/index.html'},
 {'slug': 'dibujo-cuadricula',
  'title': 'Descubre el dibujo',
  'description': 'Pinta una cuadrícula pequeña siguiendo las pistas de sus filas y columnas.',
  'category': 'Lógica',
  'target_url': '/static/juegos/dibujo-cuadricula/index.html'},
 {'slug': 'codigo-secreto',
  'title': 'Código secreto',
  'description': 'Deduce una combinación de tres figuras usando las pistas de cada intento.',
  'category': 'Lógica',
  'target_url': '/static/juegos/codigo-secreto/index.html'},
 {'slug': 'tesoro',
  'title': 'El tesoro escondido',
  'description': 'Explora casillas y usa las distancias para localizar el tesoro.',
  'category': 'Orientación',
  'target_url': '/static/juegos/tesoro/index.html'}]


def upgrade():
    op.create_table(
        'games_apps',
        sa.Column('id', sa.Uuid(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', sa.Uuid(), sa.ForeignKey('admin.tenants.id'), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('title', sa.String(150), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('item_type', sa.String(20), nullable=False, server_default='game'),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('target_url', sa.String(500)),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('featured', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('min_tier', sa.String(50), nullable=False, server_default='gratuito'),
        sa.Column('created_by', sa.Uuid()),
        sa.Column('created_by_name', sa.String(120)),
        sa.Column('updated_by', sa.Uuid()),
        sa.Column('updated_by_name', sa.String(120)),
        sa.Column('deleted_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('tenant_id', 'slug', name='games_apps_tenant_slug_uq'),
        sa.CheckConstraint("item_type IN ('game', 'app')", name='games_apps_type_ck'),
        sa.CheckConstraint("min_tier IN ('gratuito', 'dorado', 'platino')", name='games_apps_tier_ck'),
        schema='admin',
    )
    op.create_index('games_apps_tenant_active_idx', 'games_apps', ['tenant_id', 'is_active'], schema='admin')
    op.execute('''CREATE TRIGGER games_apps_updated_at BEFORE UPDATE ON admin.games_apps
                  FOR EACH ROW EXECUTE FUNCTION admin.tg_set_updated_at()''')

    # Solo inserta el catálogo en la nueva tabla; los módulos previos no se tocan.
    tenants = sa.table('tenants', sa.column('id', sa.Uuid()), schema='admin')
    games = sa.table('games_apps',
                     sa.column('tenant_id', sa.Uuid()), sa.column('slug', sa.String()),
                     sa.column('title', sa.String()), sa.column('description', sa.Text()),
                     sa.column('category', sa.String()), sa.column('target_url', sa.String()),
                     schema='admin')
    keys = ('slug', 'title', 'description', 'category', 'target_url')
    catalog = sa.union_all(*(
        sa.select(*(sa.literal(game[key]).label(key) for key in keys))
        for game in INITIAL_GAMES
    )).subquery('initial_games')
    op.execute(sa.insert(games).from_select(
        ['tenant_id', *keys],
        sa.select(tenants.c.id, *(catalog.c[key] for key in keys))
        .select_from(tenants.join(catalog, sa.true()))))

    op.execute('ALTER TABLE admin.games_apps ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE admin.games_apps FORCE ROW LEVEL SECURITY')
    op.execute('''CREATE POLICY tenant_isolation ON admin.games_apps
                  USING (tenant_id = admin.current_tenant_id())
                  WITH CHECK (tenant_id = admin.current_tenant_id())''')
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON admin.games_apps TO agecare_admin_api, agecare_admin_jobs')
    op.execute('GRANT SELECT ON admin.games_apps TO agecare_admin_ro')

    op.drop_constraint('admin_role_permissions_module_check', 'admin_role_permissions', schema='admin', type_='check')
    op.create_check_constraint('admin_role_permissions_module_check', 'admin_role_permissions',
        "module IN ('metrics','ops','support','content','marketplace','moderation','settings','legal','staff','audit','games')",
        schema='admin')
    op.execute('''INSERT INTO admin.admin_role_permissions (role_code, module, access)
                  SELECT code, 'games', CASE WHEN code IN ('admin', 'editor') THEN 'write' ELSE 'read' END
                  FROM admin.admin_roles
                  WHERE code IN ('admin','analyst','support','editor','moderator')
                  ON CONFLICT (role_code, module) DO NOTHING''')


def downgrade():
    raise RuntimeError('Migración de juegos irreversible: respaldar y revisar antes de retirar el catálogo.')
