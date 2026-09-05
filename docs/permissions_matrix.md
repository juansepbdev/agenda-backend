# Matriz de permisos

| Rol | Agenda | Chat | Gestión | Configuración |
|---|---|---|---|---|
| ADMIN | Todos los eventos de su empresa | Todas las conversaciones de su empresa; reasigna | Usuarios, asesores, clientes y supervisiones | Total del tenant |
| SUPERVISOR | Sus asesores y su propia agenda | Las de sus supervisados y las sin asignar; reasigna dentro de su equipo | Según configuración de la empresa | No |
| ADVISOR | Solo su agenda | Solo sus conversaciones; solo puede tomarlas para sí mismo | Clientes asociados y disponibilidad propia según configuración | No |

Ningún rol de tenant puede consultar otra empresa. Los permisos de objeto se aplican mediante los selectores de eventos y relaciones de supervisión.
