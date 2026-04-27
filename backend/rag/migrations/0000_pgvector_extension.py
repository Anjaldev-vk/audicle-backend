from django.db import migrations


class Migration(migrations.Migration):
    """
    Ensure pgvector extension exists before the EmbeddingChunk
    table is created. This runs before 0001_initial.
    """
    initial = True
    dependencies = []

    operations = [
        migrations.RunSQL(
            sql='CREATE EXTENSION IF NOT EXISTS vector;',
            reverse_sql='DROP EXTENSION IF EXISTS vector;',
        ),
    ]
