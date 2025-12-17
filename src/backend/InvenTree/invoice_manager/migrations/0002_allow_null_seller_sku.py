from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('invoice_manager', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invoiceitem',
            name='seller_sku',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
