# Generated migration file

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('part', '0143_alter_part_image'),
        ('company', '0076_alter_company_image'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('invoice_number', models.CharField(max_length=100, unique=True)),
                ('invoice_date', models.DateField()),
                ('invoice_file', models.FileField(upload_to='invoices/%Y/%m/')),
                ('invoice_data', models.JSONField(default=dict, help_text='Raw extracted invoice data')),
                ('total_net_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('total_vat_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('invoice_total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed'), ('CANCELLED', 'Cancelled')], default='PENDING', max_length=20)),
                ('processing_log', models.TextField(blank=True, help_text='Log of processing steps')),
                ('error_message', models.TextField(blank=True, help_text='Error details if processing failed')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invoices', to='company.company')),
                ('uploaded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_invoices', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Invoice',
                'verbose_name_plural': 'Invoices',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='InvoiceProcessingLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('UPLOAD', 'Upload'), ('EXTRACT', 'Extract'), ('MATCH', 'Match Part'), ('STOCK_CREATE', 'Stock Created'), ('STOCK_ERROR', 'Stock Error'), ('ERROR', 'Error')], max_length=20)),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='processing_logs', to='invoice_manager.invoice')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='InvoiceItem',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField()),
                ('seller_sku', models.CharField(blank=True, max_length=100)),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('total_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('tax_rate', models.DecimalField(decimal_places=2, default=20, max_digits=5)),
                ('matched', models.BooleanField(default=False, help_text='Whether part has been matched')),
                ('notes', models.TextField(blank=True)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='invoice_manager.invoice')),
                ('part', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='part.part')),
            ],
            options={
                'ordering': ['invoice', 'id'],
                'unique_together': {('invoice', 'seller_sku', 'description')},
            },
        ),
    ]
