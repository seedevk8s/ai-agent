FROM nginx:1.27-alpine

COPY Docker/front/nginx.conf /etc/nginx/conf.d/default.conf
COPY front/index.html front/styles.css front/app.js /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
