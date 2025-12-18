"""Admin for the common app."""

import json
import os
import subprocess

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path

from . import models, validators


@admin.register(models.Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    """Admin interface for Attachment objects."""

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Provide custom choices for 'model_type' field."""
        if db_field.name == 'model_type':
            db_field.choices = validators.attachment_model_options()

        return super().formfield_for_dbfield(db_field, request, **kwargs)

    list_display = (
        'model_type',
        'model_id',
        'attachment',
        'link',
        'upload_user',
        'upload_date',
    )

    list_filter = ['model_type', 'upload_user']

    readonly_fields = ['file_size', 'upload_date', 'upload_user']

    search_fields = ('content_type', 'comment')

    change_list_template = 'admin/common/attachment/change_list.html'

    def get_urls(self):
        """Add custom URLs for backup/restore."""
        urls = super().get_urls()
        custom_urls = [
            path(
                'backup-create/',
                self.admin_site.admin_view(self.backup_create_view),
                name='backup-create',
            ),
            path(
                'backup-restore/',
                self.admin_site.admin_view(self.backup_restore_view),
                name='backup-restore',
            ),
        ]
        return custom_urls + urls

    def backup_create_view(self, request):
        """View to create backup."""
        from datetime import datetime

        from django import forms
        from django.shortcuts import render

        class BackupForm(forms.Form):
            backup_name = forms.CharField(
                label='Backup Name / Comment',
                max_length=100,
                required=False,
                initial=f'backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}',
                widget=forms.TextInput(
                    attrs={
                        'style': 'width: 100%; padding: 15px; font-size: 16px; border: 2px solid #333; border-radius: 4px;',
                        'placeholder': 'e.g., before-update, monthly-backup, etc.',
                    }
                ),
            )

        if request.method == 'POST':
            form = BackupForm(request.POST)
            if form.is_valid():
                backup_name = (
                    form.cleaned_data['backup_name']
                    or f'backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
                )
                try:
                    cwd = os.environ.get('INVENTREE_ROOT', '/home/inventree')
                    backup_dir = os.environ.get(
                        'INVENTREE_BACKUP_DIR', '/home/inventree/dev/backup'
                    )

                    # Get list of existing backups before creating new one
                    existing_backups = set()
                    if os.path.exists(backup_dir):
                        existing_backups = {
                            f
                            for f in os.listdir(backup_dir)
                            if f.endswith('.psql.bin.gz')
                        }

                    result = subprocess.run(
                        'invoke backup',
                        check=False,
                        cwd=cwd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=3600,
                    )

                    if result.returncode == 0:
                        # Find the newly created backup file
                        if os.path.exists(backup_dir):
                            new_backups = {
                                f
                                for f in os.listdir(backup_dir)
                                if f.endswith('.psql.bin.gz')
                            }
                            new_files = new_backups - existing_backups

                            if new_files:
                                new_backup_file = list(new_files)[0]

                                # Save metadata
                                metadata_file = os.path.join(
                                    backup_dir, 'backup_metadata.json'
                                )
                                metadata = {}
                                if os.path.exists(metadata_file):
                                    try:
                                        with open(metadata_file, encoding='utf-8') as f:
                                            metadata = json.load(f)
                                    except:
                                        metadata = {}

                                metadata[new_backup_file] = {
                                    'custom_name': backup_name,
                                    'created_at': datetime.now().strftime(
                                        '%Y-%m-%d %H:%M:%S'
                                    ),
                                }

                                with open(metadata_file, 'w', encoding='utf-8') as f:
                                    json.dump(metadata, f, indent=2, ensure_ascii=False)

                        messages.success(
                            request, f'✅ Backup "{backup_name}" created successfully'
                        )
                    else:
                        messages.error(request, f'❌ Backup failed: {result.stderr}')
                except Exception as e:
                    messages.error(request, f'❌ Backup error: {e!s}')

                return HttpResponseRedirect('../')
        else:
            form = BackupForm()

        backup_dir = os.environ.get(
            'INVENTREE_BACKUP_DIR', '/home/inventree/dev/backup'
        )

        context = {
            'form': form,
            'title': 'Create Database Backup',
            'backup_dir': backup_dir,
            'site_header': 'InvenTree Admin',
            'has_permission': True,
        }

        return render(request, 'admin/common/create_backup.html', context)

    def backup_restore_view(self, request):
        """View to restore backup."""
        from django import forms
        from django.shortcuts import render

        backup_dir = os.environ.get(
            'INVENTREE_BACKUP_DIR', '/home/inventree/dev/backup'
        )

        # Load metadata
        metadata = {}
        metadata_file = os.path.join(backup_dir, 'backup_metadata.json')
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, encoding='utf-8') as f:
                    metadata = json.load(f)
            except:
                metadata = {}

        # Get list of backup files with metadata
        db_backups = []
        if os.path.exists(backup_dir):
            for f in sorted(os.listdir(backup_dir), reverse=True):
                # Include all PostgreSQL backup files
                if f.endswith('.psql.bin.gz') or (
                    f.startswith('InvenTree-db-') and f.endswith('.gz')
                ):
                    filepath = os.path.join(backup_dir, f)
                    size_mb = os.path.getsize(filepath) / (1024 * 1024)

                    # Add custom name if exists in metadata
                    custom_name = metadata.get(f, {}).get('custom_name', '')
                    if custom_name:
                        label = f'{f} ({size_mb:.2f} MB) - "{custom_name}"'
                    else:
                        label = f'{f} ({size_mb:.2f} MB)'

                    db_backups.append((f, label))

        class RestoreForm(forms.Form):
            backup_file = forms.ChoiceField(
                choices=db_backups,
                label='Select Backup File',
                widget=forms.Select(attrs={'style': 'width: 100%; padding: 8px;'}),
            )

        if request.method == 'POST':
            form = RestoreForm(request.POST)
            if form.is_valid():
                selected_file = form.cleaned_data['backup_file']
                try:
                    cwd = os.environ.get('INVENTREE_ROOT', '/home/inventree')
                    result = subprocess.run(
                        f'invoke restore --db-file={selected_file}',
                        check=False,
                        cwd=cwd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=3600,
                    )

                    if result.returncode == 0:
                        messages.success(
                            request,
                            f'✅ Restore from {selected_file} completed successfully',
                        )
                    else:
                        messages.error(request, f'❌ Restore failed: {result.stderr}')
                except Exception as e:
                    messages.error(request, f'❌ Restore error: {e!s}')

                return HttpResponseRedirect('../')
        else:
            form = RestoreForm()

        context = {
            'form': form,
            'title': 'Restore Database Backup',
            'backup_dir': backup_dir,
            'site_header': 'InvenTree Admin',
            'has_permission': True,
        }

        return render(request, 'admin/common/restore_backup.html', context)

    def action_create_backup(self, request, queryset):
        """Action to create backup."""
        try:
            cwd = os.environ.get('INVENTREE_ROOT', '/home/inventree')
            result = subprocess.run(
                'invoke backup',
                check=False,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3600,
            )

            if result.returncode == 0:
                messages.success(request, '✅ Backup created successfully')
            else:
                messages.error(request, f'❌ Backup failed: {result.stderr}')
        except Exception as e:
            messages.error(request, f'❌ Backup error: {e!s}')

    action_create_backup.short_description = '📦 Create Database Backup'
    action_create_backup.permissions = []
    action_create_backup.allow_empty_queryset = True

    def action_restore_backup(self, request, queryset):
        """Action to restore from latest backup."""
        try:
            cwd = os.environ.get('INVENTREE_ROOT', '/home/inventree')
            result = subprocess.run(
                'invoke restore',
                check=False,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3600,
            )

            if result.returncode == 0:
                messages.success(request, '✅ Restore completed successfully')
            else:
                messages.error(request, f'❌ Restore failed: {result.stderr}')
        except Exception as e:
            messages.error(request, f'❌ Restore error: {e!s}')

    action_restore_backup.short_description = '📥 Restore from Latest Backup'
    action_restore_backup.permissions = []
    action_restore_backup.allow_empty_queryset = True


@admin.register(models.DataOutput)
class DataOutputAdmin(admin.ModelAdmin):
    """Admin interface for DataOutput objects."""

    list_display = ('user', 'created', 'output_type', 'output')

    list_filter = ('user', 'output_type')


@admin.register(models.BarcodeScanResult)
class BarcodeScanResultAdmin(admin.ModelAdmin):
    """Admin interface for BarcodeScanResult objects."""

    list_display = ('data', 'timestamp', 'user', 'endpoint', 'result')

    list_filter = ('user', 'endpoint', 'result')


@admin.register(models.ProjectCode)
class ProjectCodeAdmin(admin.ModelAdmin):
    """Admin settings for ProjectCode."""

    list_display = ('code', 'description')

    search_fields = ('code', 'description')


@admin.register(models.InvenTreeSetting)
class SettingsAdmin(admin.ModelAdmin):
    """Admin settings for InvenTreeSetting."""

    list_display = ('key', 'value')

    def get_readonly_fields(self, request, obj=None):  # pragma: no cover
        """Prevent the 'key' field being edited once the setting is created."""
        if obj:
            return ['key']
        return []


@admin.register(models.InvenTreeUserSetting)
class UserSettingsAdmin(admin.ModelAdmin):
    """Admin settings for InvenTreeUserSetting."""

    list_display = ('key', 'value', 'user')

    def get_readonly_fields(self, request, obj=None):  # pragma: no cover
        """Prevent the 'key' field being edited once the setting is created."""
        if obj:
            return ['key']
        return []


@admin.register(models.WebhookEndpoint)
class WebhookAdmin(admin.ModelAdmin):
    """Admin settings for Webhook."""

    list_display = ('endpoint_id', 'name', 'active', 'user')


@admin.register(models.NotificationEntry)
class NotificationEntryAdmin(admin.ModelAdmin):
    """Admin settings for NotificationEntry."""

    list_display = ('key', 'uid', 'updated')


@admin.register(models.NotificationMessage)
class NotificationMessageAdmin(admin.ModelAdmin):
    """Admin settings for NotificationMessage."""

    list_display = (
        'age_human',
        'user',
        'category',
        'name',
        'read',
        'target_object',
        'source_object',
    )

    list_filter = ('category', 'read', 'user')

    search_fields = ('name', 'category', 'message')


@admin.register(models.NewsFeedEntry)
class NewsFeedEntryAdmin(admin.ModelAdmin):
    """Admin settings for NewsFeedEntry."""

    list_display = ('title', 'author', 'published', 'summary')


admin.site.register(models.WebhookMessage, admin.ModelAdmin)
admin.site.register(models.EmailMessage, admin.ModelAdmin)
admin.site.register(models.EmailThread, admin.ModelAdmin)
