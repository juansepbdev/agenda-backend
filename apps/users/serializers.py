from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "phone", "role", "is_active", "password", "created_at")
        read_only_fields = ("id", "is_active", "created_at")

    def create(self, validated_data):
        # `password` es required=False: hacer pop() sin defecto reventaba con
        # KeyError (500) al crear un usuario sin contraseña.
        password = validated_data.pop("password", None)
        actor = self.context["request"].user
        user = User.objects.create_user(
            company=actor.company,
            created_by=actor,
            updated_by=actor,
            password=password,
            **validated_data,
        )
        if not password:
            # Sin contraseña utilizable el usuario existe pero no puede entrar,
            # que es lo correcto hasta que un admin se la asigne.
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.updated_by = self.context["request"].user
        instance.full_clean()
        instance.save()
        return instance
