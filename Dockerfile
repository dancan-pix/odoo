FROM odoo:18.0
USER root
COPY config/odoo.conf /etc/odoo/odoo.conf
RUN chown odoo:odoo /etc/odoo/odoo.conf && chmod 0644 /etc/odoo/odoo.conf
USER odoo
EXPOSE 8069
